"""Curated tools catalog for tools-pool-based task generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any


@dataclass(slots=True)
class ToolEntry:
    tool_id: str
    name: str
    capabilities: list[str]
    typical_use: str
    install: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_CATALOG: list[dict[str, Any]] = [
    {
        "tool_id": "python3-struct",
        "name": "struct (Python stdlib)",
        "capabilities": ["binary-parsing", "binary-construction", "endian-handling"],
        "typical_use": "Parse or construct binary file formats (STL, WAV, custom protocols)",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-csv",
        "name": "csv (Python stdlib)",
        "capabilities": ["csv-reading", "csv-writing", "delimiter-handling"],
        "typical_use": "Read and write CSV/TSV tabular data files",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-json",
        "name": "json (Python stdlib)",
        "capabilities": ["json-parsing", "json-serialization"],
        "typical_use": "Parse and write JSON data; standard task output format",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-re",
        "name": "re (Python stdlib)",
        "capabilities": ["regex-matching", "text-extraction", "pattern-search"],
        "typical_use": "Extract structured data from unstructured text, logs, or documents",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-math",
        "name": "math (Python stdlib)",
        "capabilities": ["trigonometry", "logarithms", "basic-arithmetic", "constants"],
        "typical_use": "Mathematical computations, unit conversions, geometric calculations",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-statistics",
        "name": "statistics (Python stdlib)",
        "capabilities": ["mean", "median", "stdev", "variance", "quantiles"],
        "typical_use": "Descriptive statistics on numeric data series",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-collections",
        "name": "collections (Python stdlib)",
        "capabilities": ["counter", "defaultdict", "deque", "ordered-dict"],
        "typical_use": "Frequency counting, adjacency lists, BFS/DFS queues, grouping data",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-pathlib",
        "name": "pathlib (Python stdlib)",
        "capabilities": ["path-manipulation", "file-discovery", "glob-patterns"],
        "typical_use": "File system navigation, directory listing, path construction",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-xml",
        "name": "xml.etree.ElementTree (Python stdlib)",
        "capabilities": ["xml-parsing", "xml-construction", "xpath-basic"],
        "typical_use": "Parse XML, GPX, or SVG files; extract elements by tag or attribute",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-wave",
        "name": "wave (Python stdlib)",
        "capabilities": ["wav-reading", "wav-writing", "pcm-sample-access"],
        "typical_use": "Read/write WAV audio files, access raw PCM sample frames",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-zipfile",
        "name": "zipfile (Python stdlib)",
        "capabilities": ["zip-reading", "zip-writing", "archive-extraction"],
        "typical_use": "Process ZIP archives, extract or create compressed bundles",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-datetime",
        "name": "datetime (Python stdlib)",
        "capabilities": ["date-parsing", "time-arithmetic", "timezone-handling", "iso-format"],
        "typical_use": "Parse timestamps, compute durations, schedule-based calculations",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-subprocess",
        "name": "subprocess (Python stdlib)",
        "capabilities": ["command-execution", "pipe-handling", "process-management"],
        "typical_use": "Run external CLI tools (ffmpeg, git, gcc) from Python",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-difflib",
        "name": "difflib (Python stdlib)",
        "capabilities": ["text-diff", "sequence-matching", "similarity-ratio"],
        "typical_use": "Compare text files, find differences, compute edit distances",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-sqlite3",
        "name": "sqlite3 (Python stdlib)",
        "capabilities": ["sql-queries", "local-database", "table-creation"],
        "typical_use": "Store and query structured data in a local SQLite database",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-hashlib",
        "name": "hashlib (Python stdlib)",
        "capabilities": ["md5", "sha256", "file-checksums"],
        "typical_use": "Compute file hashes, verify data integrity, detect duplicates",
        "install": "stdlib",
    },
    {
        "tool_id": "python3-shutil",
        "name": "shutil (Python stdlib)",
        "capabilities": ["file-copy", "directory-tree-copy", "archive-creation"],
        "typical_use": "Bulk file operations, directory management, archiving",
        "install": "stdlib",
    },
    {
        "tool_id": "numpy",
        "name": "NumPy",
        "capabilities": ["array-operations", "linear-algebra", "fft", "random-generation", "statistics"],
        "typical_use": "Numerical computing, matrix operations, signal processing, large-array statistics",
        "install": "pip install numpy",
    },
    {
        "tool_id": "scipy",
        "name": "SciPy",
        "capabilities": ["optimization", "interpolation", "signal-processing", "spatial-analysis", "statistical-tests"],
        "typical_use": "Scientific computing, curve fitting, filtering, hypothesis testing",
        "install": "pip install scipy",
    },
    {
        "tool_id": "pandas",
        "name": "pandas",
        "capabilities": ["dataframe", "csv-excel-io", "groupby", "merge-join", "pivot-table", "time-series"],
        "typical_use": "Tabular data manipulation, aggregation, pivoting, time-series analysis",
        "install": "pip install pandas",
    },
    {
        "tool_id": "networkx",
        "name": "NetworkX",
        "capabilities": ["graph-construction", "shortest-path", "connected-components", "centrality", "traversal"],
        "typical_use": "Graph/network analysis, dependency resolution, community detection",
        "install": "pip install networkx",
    },
    {
        "tool_id": "pillow",
        "name": "Pillow (PIL)",
        "capabilities": ["image-read-write", "resize-crop", "pixel-access", "format-conversion"],
        "typical_use": "Image processing, OCR preprocessing, format conversion (PNG/JPEG/TIFF)",
        "install": "pip install Pillow",
    },
    {
        "tool_id": "openpyxl",
        "name": "openpyxl",
        "capabilities": ["xlsx-reading", "xlsx-writing", "cell-formatting", "chart-access"],
        "typical_use": "Read/write Excel .xlsx files, extract tabular data from spreadsheets",
        "install": "pip install openpyxl",
    },
    {
        "tool_id": "pypdf",
        "name": "pypdf",
        "capabilities": ["pdf-text-extraction", "pdf-metadata", "page-manipulation"],
        "typical_use": "Extract text and metadata from PDF documents",
        "install": "pip install pypdf",
    },
    {
        "tool_id": "beautifulsoup4",
        "name": "BeautifulSoup4",
        "capabilities": ["html-parsing", "css-selectors", "tag-navigation", "text-extraction"],
        "typical_use": "Parse HTML/XML documents, scrape structured data from markup",
        "install": "pip install beautifulsoup4 lxml",
    },
    {
        "tool_id": "jinja2",
        "name": "Jinja2",
        "capabilities": ["template-rendering", "html-generation", "text-formatting"],
        "typical_use": "Generate HTML reports, formatted documents, or code from templates",
        "install": "pip install Jinja2",
    },
    {
        "tool_id": "scikit-learn",
        "name": "scikit-learn",
        "capabilities": ["classification", "clustering", "regression", "feature-extraction", "tfidf"],
        "typical_use": "ML tasks, TF-IDF text search, anomaly detection, K-means clustering",
        "install": "pip install scikit-learn",
    },
    {
        "tool_id": "matplotlib",
        "name": "Matplotlib",
        "capabilities": ["line-plots", "bar-charts", "scatter-plots", "figure-export"],
        "typical_use": "Generate static charts and visualizations as PNG/SVG files",
        "install": "pip install matplotlib",
    },
    {
        "tool_id": "python-pptx",
        "name": "python-pptx",
        "capabilities": ["pptx-reading", "pptx-writing", "slide-manipulation", "text-extraction"],
        "typical_use": "Read/write PowerPoint files, extract or insert slide content",
        "install": "pip install python-pptx",
    },
    {
        "tool_id": "sympy",
        "name": "SymPy",
        "capabilities": ["symbolic-math", "equation-solving", "calculus", "expression-simplification"],
        "typical_use": "Symbolic mathematics, algebraic manipulation, symbolic integration",
        "install": "pip install sympy",
    },
    {
        "tool_id": "pyyaml",
        "name": "PyYAML",
        "capabilities": ["yaml-parsing", "yaml-writing"],
        "typical_use": "Parse and write YAML configuration or data files",
        "install": "pip install pyyaml",
    },
    {
        "tool_id": "ffmpeg",
        "name": "FFmpeg (system tool)",
        "capabilities": ["video-transcoding", "audio-extraction", "format-conversion", "stream-split"],
        "typical_use": "Process audio/video files, extract audio tracks, convert formats",
        "install": "apt install ffmpeg",
    },
]


def load_tools_catalog() -> list[ToolEntry]:
    """Load the curated tools catalog."""
    return [ToolEntry(**entry) for entry in _CATALOG]


def format_tools_for_prompt(tools: list[ToolEntry]) -> str:
    """Serialize tools catalog as JSON for inclusion in LLM prompts."""
    return json.dumps([t.to_dict() for t in tools], indent=2, ensure_ascii=False)
