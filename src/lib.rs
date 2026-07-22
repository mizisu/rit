use lumis::highlight::highlight_iter;
use lumis::languages::Language;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use std::io;

const MAX_SPANS: usize = 250_000;

type SemanticSpan = (usize, usize, String);

fn collect_spans(source: &str, language_hint: Option<&str>) -> Result<Vec<SemanticSpan>, String> {
    let language = Language::guess(language_hint, source);
    let mut spans: Vec<SemanticSpan> = Vec::new();

    highlight_iter(
        source,
        language,
        None,
        |_text, _injected_language, range, scope, _style| -> Result<(), io::Error> {
            if scope.is_empty() || range.start >= range.end {
                return Ok(());
            }

            if let Some(previous) = spans.last_mut() {
                if previous.1 == range.start && previous.2 == scope {
                    previous.1 = range.end;
                    return Ok(());
                }
            }

            if spans.len() >= MAX_SPANS {
                return Err(io::Error::other("highlight span limit exceeded"));
            }
            spans.push((range.start, range.end, scope.to_owned()));
            Ok(())
        },
    )
    .map_err(|error| error.to_string())?;

    Ok(spans)
}

#[pyfunction(signature = (source, language_hint=None))]
fn highlight_spans(
    py: Python<'_>,
    source: String,
    language_hint: Option<String>,
) -> PyResult<Vec<SemanticSpan>> {
    py.detach(move || collect_spans(&source, language_hint.as_deref()))
        .map_err(PyRuntimeError::new_err)
}

#[pymodule]
fn _lumis(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(highlight_spans, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::collect_spans;

    #[test]
    fn highlights_python_with_semantic_scopes() {
        let spans = collect_spans("def greet(name):\n    return name\n", Some("python"))
            .expect("Python highlighting should succeed");

        assert!(spans.iter().any(|span| span.2.starts_with("function")));
        assert!(spans.iter().any(|span| span.2 == "variable"));
    }

    #[test]
    fn unknown_plain_text_has_no_semantic_spans() {
        let spans = collect_spans("ordinary prose", Some("notes.unknown"))
            .expect("plain text highlighting should succeed");

        assert!(spans.is_empty());
    }
}
