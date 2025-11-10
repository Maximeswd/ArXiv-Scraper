from rich.style import Style
from rich.text import Text
import re

ACTIVE_THEME = "nordic"

THEMES = {
    "vibrant": {
        "title": "purple",
        "author": "magenta",
        "subjects": "yellow",
        "link": "bright_blue",
        "abstract": "green",
    },
    "solarized": {
        "title": "yellow",
        "author": "cyan",
        "subjects": "green",
        "link": "blue",
        "abstract": "white",
    },
    "classic": {
        "title": "bright_white",
        "author": "green",
        "subjects": "yellow",
        "link": "bright_blue",
        "abstract": "white",
    },
    "nordic": {
        "title": "cyan1",
        "author": "magenta3",
        "subjects": "light_slate_grey",
        "link": "slate_blue1",
        "abstract": "bright_white",
    },
}


def clean_abstract(abstract):
    if isinstance(abstract, str) and "\\\\" in abstract:
        valid = abstract.split("\\\\")[1:]
        return " ".join(valid).strip()
    return abstract.strip() if isinstance(abstract, str) else ""


def get_until(i, lines, delim, n_skip=0):
    text = lines[i][n_skip:].strip() + " "
    i += 1
    try:
        while not lines[i].startswith(delim):
            text += lines[i].strip() + " "
            i += 1
    except IndexError:
        return text
    return text


def add_to_table(df, table, keywords_to_highlight):
    try:
        colors = THEMES[ACTIVE_THEME]
    except KeyError:
        print(f"Warning: Theme '{ACTIVE_THEME}' not found. Falling back to 'vibrant'.")
        colors = THEMES["vibrant"]

    title_style = Style(color=colors["title"], bold=True)
    author_style = Style(color=colors["author"])
    subject_style = Style(color=colors["subjects"], italic=True)
    link_style = Style(color=colors["link"])
    abstract_style = Style(color=colors["abstract"])
    highlight_style = "bold underline"

    for _, row in df.iterrows():
        title_text = Text(row.get("title", ""))
        authors_text = Text(row.get("authors", ""))
        subjects_text = Text(row.get("subjects", "No subjects found"))
        abstract_text = Text(clean_abstract(row.get("abstract", "")))

        if keywords_to_highlight:
            for keyword in keywords_to_highlight:
                if isinstance(keyword, str):
                    highlight_regex = f"(?i)\\b({re.escape(keyword)})\\b"

                    title_text.highlight_regex(highlight_regex, style=highlight_style)
                    abstract_text.highlight_regex(
                        highlight_regex, style=highlight_style
                    )
                    authors_text.highlight_regex(highlight_regex, style=highlight_style)

        table.add_row("", title_text, style=title_style)
        table.add_row("", authors_text, style=author_style)
        table.add_row("", row.get("url", ""), style=link_style)
        table.add_row("", subjects_text, style=subject_style)
        table.add_row("", abstract_text, style=abstract_style)
        table.add_row("")
    return table
