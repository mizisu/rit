import pytest
from rit.core.diff import parse_multi_file_patch, parse_multi_file_patch_summaries


@pytest.mark.parametrize(
    ("patch", "filename", "old_filename"),
    [
        (
            (
                'diff --git "a/\\355\\225\\234\\352\\270\\200.py" "b/\\355\\225\\234\\352\\270\\200.py"\n'
                '--- "a/\\355\\225\\234\\352\\270\\200.py"\n'
                '+++ "b/\\355\\225\\234\\352\\270\\200.py"\n'
                "@@ -1 +1 @@\n-old\n+new\n"
            ),
            "한글.py",
            None,
        ),
        (
            (
                'diff --git a/old name.py "b/new\\tname.py"\n'
                'rename from old name.py\nrename to "new\\tname.py"\n'
            ),
            "new\tname.py",
            "old name.py",
        ),
        (
            (
                'diff --git "a/old\\tname.py" b/new name.py\n'
                'copy from "old\\tname.py"\ncopy to new name.py\n'
            ),
            "new name.py",
            "old\tname.py",
        ),
        (
            (
                'diff --git "a/\\"quote\\".py" "b/\\"quote\\".py"\n'
                "old mode 100644\nnew mode 100755\n"
            ),
            '"quote".py',
            None,
        ),
        (
            (
                'diff --git "a/a\\\\b.py" "b/a\\\\b.py"\n'
                "old mode 100644\nnew mode 100755\n"
            ),
            "a\\b.py",
            None,
        ),
        (
            (
                "diff --git a/file with spaces.py b/file with spaces.py\n"
                "old mode 100644\nnew mode 100755\n"
            ),
            "file with spaces.py",
            None,
        ),
        (
            (
                "diff --git a/dir b/file.py b/dir b/file.py\n"
                "old mode 100644\nnew mode 100755\n"
            ),
            "dir b/file.py",
            None,
        ),
        (
            (
                "diff --git a/dir b/asset.bin b/dir b/asset.bin\n"
                "Binary files a/dir b/asset.bin and b/dir b/asset.bin differ\n"
            ),
            "dir b/asset.bin",
            None,
        ),
    ],
)
def test_canonical_patch_paths_match_graphql_paths(
    patch: str, filename: str, old_filename: str | None
) -> None:
    summary = parse_multi_file_patch_summaries(patch)[0]
    parsed = parse_multi_file_patch(patch)[0]

    assert summary.filename == parsed.diff.filename == filename
    assert summary.old_filename == parsed.diff.old_filename == old_filename
    assert summary.patch == parsed.patch == patch.rstrip("\n")
