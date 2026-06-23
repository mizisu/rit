"""Nerd Font icons for file types. Reference: https://www.nerdfonts.com/cheat-sheet"""

__all__ = (
    "DEFAULT_FILE_ICON",
    "DIR_ICON",
    "get_file_icon",
)


DEFAULT_FILE_ICON = "\uf15b"  # nf-fa-file
DIR_ICON = "\uf07b"  # nf-fa-folder

EXTENSION_ICONS: dict[str, str] = {
    ".py": "\ue73c",  # nf-dev-python
    ".pyi": "\ue73c",
    ".pyx": "\ue73c",
    ".pyw": "\ue73c",
    ".js": "\ue74e",  # nf-dev-javascript
    ".mjs": "\ue74e",
    ".cjs": "\ue74e",
    ".jsx": "\ue7ba",  # nf-dev-react
    ".ts": "\ue628",  # nf-seti-typescript
    ".tsx": "\ue7ba",  # nf-dev-react
    ".html": "\ue736",  # nf-dev-html5
    ".htm": "\ue736",
    ".css": "\ue749",  # nf-dev-css3
    ".scss": "\ue749",
    ".sass": "\ue749",
    ".less": "\ue749",
    ".json": "\ue60b",  # nf-seti-json
    ".yaml": "\ue615",  # nf-seti-yaml
    ".yml": "\ue615",
    ".toml": "\ue615",
    ".xml": "\ue619",  # nf-seti-xml
    ".ini": "\uf013",  # nf-fa-gear
    ".cfg": "\uf013",
    ".conf": "\uf013",
    ".env": "\uf462",  # nf-oct-file_code
    ".md": "\ue73e",  # nf-dev-markdown
    ".mdx": "\ue73e",
    ".rst": "\uf15c",  # nf-fa-file_text
    ".txt": "\uf15c",
    ".sh": "\ue795",  # nf-dev-terminal
    ".bash": "\ue795",
    ".zsh": "\ue795",
    ".fish": "\ue795",
    ".ps1": "\ue795",
    ".go": "\ue626",  # nf-seti-go
    ".rs": "\ue7a8",  # nf-dev-rust
    ".java": "\ue738",  # nf-dev-java
    ".kt": "\ue634",  # nf-seti-kotlin
    ".kts": "\ue634",
    ".c": "\ue61e",  # nf-seti-c
    ".h": "\ue61e",
    ".cpp": "\ue61d",  # nf-seti-cpp
    ".hpp": "\ue61d",
    ".cc": "\ue61d",
    ".cxx": "\ue61d",
    ".cs": "\uf81a",  # nf-mdi-language_csharp
    ".rb": "\ue739",  # nf-dev-ruby
    ".erb": "\ue739",
    ".rake": "\ue739",
    ".php": "\ue73d",  # nf-dev-php
    ".swift": "\ue755",  # nf-dev-swift
    ".lua": "\ue620",  # nf-seti-lua
    ".pl": "\ue769",  # nf-dev-perl
    ".pm": "\ue769",
    ".r": "\uf25d",  # nf-fa-registered (R symbol)
    ".R": "\uf25d",
    ".sql": "\uf1c0",  # nf-fa-database
    ".dockerfile": "\ue7b0",  # nf-dev-docker
    ".gitignore": "\ue702",  # nf-dev-git
    ".gitattributes": "\ue702",
    ".gitmodules": "\ue702",
    ".png": "\uf1c5",  # nf-fa-file_image
    ".jpg": "\uf1c5",
    ".jpeg": "\uf1c5",
    ".gif": "\uf1c5",
    ".svg": "\uf1c5",
    ".ico": "\uf1c5",
    ".webp": "\uf1c5",
    ".lock": "\uf023",  # nf-fa-lock
    ".zip": "\uf1c6",  # nf-fa-file_archive
    ".tar": "\uf1c6",
    ".gz": "\uf1c6",
    ".rar": "\uf1c6",
    ".7z": "\uf1c6",
    ".log": "\uf15c",  # nf-fa-file_text
    ".bak": "\uf15b",
}

FILENAME_ICONS: dict[str, str] = {
    "Dockerfile": "\ue7b0",
    "docker-compose.yml": "\ue7b0",
    "docker-compose.yaml": "\ue7b0",
    ".dockerignore": "\ue7b0",
    ".gitignore": "\ue702",
    ".gitattributes": "\ue702",
    ".gitmodules": "\ue702",
    "package.json": "\ue71e",  # nf-dev-nodejs_small
    "package-lock.json": "\ue71e",
    "yarn.lock": "\ue71e",
    "pnpm-lock.yaml": "\ue71e",
    "Cargo.toml": "\ue7a8",
    "Cargo.lock": "\ue7a8",
    "go.mod": "\ue626",
    "go.sum": "\ue626",
    "requirements.txt": "\ue73c",
    "pyproject.toml": "\ue73c",
    "setup.py": "\ue73c",
    "Pipfile": "\ue73c",
    "Pipfile.lock": "\ue73c",
    "poetry.lock": "\ue73c",
    "Gemfile": "\ue739",
    "Gemfile.lock": "\ue739",
    "composer.json": "\ue73d",
    "composer.lock": "\ue73d",
    "Makefile": "\uf013",  # nf-fa-gear
    "CMakeLists.txt": "\uf013",
    "Rakefile": "\ue739",
    ".travis.yml": "\uf013",
    ".gitlab-ci.yml": "\uf296",  # nf-fa-gitlab
    "Jenkinsfile": "\uf013",
    ".env": "\uf462",
    ".env.local": "\uf462",
    ".env.development": "\uf462",
    ".env.production": "\uf462",
    ".editorconfig": "\uf013",
    ".prettierrc": "\uf013",
    ".eslintrc": "\uf013",
    ".eslintrc.js": "\uf013",
    ".eslintrc.json": "\uf013",
    "tsconfig.json": "\ue628",
    "jsconfig.json": "\ue74e",
    "babel.config.js": "\uf013",
    "webpack.config.js": "\uf013",
    "vite.config.js": "\uf013",
    "vite.config.ts": "\uf013",
    "README.md": "\ue73e",
    "README": "\ue73e",
    "LICENSE": "\uf15c",
    "LICENSE.md": "\uf15c",
    "CHANGELOG.md": "\uf15c",
    "CONTRIBUTING.md": "\uf15c",
    "pytest.ini": "\ue73c",
    "setup.cfg": "\ue73c",
    "tox.ini": "\ue73c",
    "jest.config.js": "\ue74e",
    "jest.config.ts": "\ue628",
}


def get_file_icon(filename: str) -> str:
    if "/" in filename:
        filename = filename.rsplit("/", 1)[-1]

    if filename in FILENAME_ICONS:
        return FILENAME_ICONS[filename]

    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
        if ext in EXTENSION_ICONS:
            return EXTENSION_ICONS[ext]

    return DEFAULT_FILE_ICON
