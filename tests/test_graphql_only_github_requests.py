import ast
from pathlib import Path


SERVICES_DIR = Path(__file__).parents[1] / "src" / "rit" / "services"
STANDALONE_REVIEW_COMMENT_MODULES = {
    "pr_file_comment_request.py",
    "pr_review_comment_request.py",
}


def test_rest_is_limited_to_standalone_review_comment_modules() -> None:
    violations: list[str] = []
    for path in SERVICES_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
                continue
            first = node.elts[0]
            if not isinstance(first, ast.Constant) or first.value != "api":
                continue
            second = node.elts[1] if len(node.elts) > 1 else None
            if isinstance(second, ast.Constant) and second.value == "graphql":
                continue
            if path.name in STANDALONE_REVIEW_COMMENT_MODULES:
                continue
            violations.append(f"{path.name}:{node.lineno}")

    assert violations == []


def test_only_standalone_review_comment_modules_contain_rest_paths() -> None:
    modules_with_rest_paths = {
        path.name
        for path in SERVICES_DIR.glob("*.py")
        if "/repos/" in path.read_text()
    }

    assert modules_with_rest_paths == STANDALONE_REVIEW_COMMENT_MODULES
