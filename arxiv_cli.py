import requests
import feedparser
import argparse
import os
import pandas as pd
import re
import subprocess
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from rich.console import Console
from rich.table import Table
from rich.style import Style
from rich.text import Text
from rich import box
from utils import add_to_table

# Setup
console = Console()
MAIL_FILE = "mail_text.txt"
SCRIPT_FILE = "fetch_arxiv.scpt"
DATE_WARNING_FLAG = ".date_format_warning_shown"

# ─── MODE 4: Netherlands (OpenAlex) ──────────────────────────────────────────
# OpenAlex resolves author affiliations, which the arXiv API does not.
# ROR IDs are the canonical institution identifiers used by OpenAlex.
NL_INSTITUTIONS = {
    "uva": ("University of Amsterdam", "https://ror.org/04dkp9463"),
    "vu": ("Vrije Universiteit Amsterdam", "https://ror.org/008xxew50"),
    "cwi": ("CWI", "https://ror.org/00x7ekv49"),
    "amolf": ("AMOLF", "https://ror.org/028rha179"),
    "tue": ("TU Eindhoven", "https://ror.org/02c2kyt77"),
    "tudelft": ("TU Delft", "https://ror.org/02e2c7k09"),
    "utwente": ("University of Twente", "https://ror.org/006hf6230"),
    "leiden": ("Leiden University", "https://ror.org/027bh9e22"),
    "utrecht": ("Utrecht University", "https://ror.org/04pp8hn57"),
    "rug": ("University of Groningen", "https://ror.org/012p63287"),
    "wur": ("Wageningen University", "https://ror.org/04qw24q55"),
}
OPENALEX_MAILTO = os.environ.get("OPENALEX_MAILTO", "")  # optional: faster "polite pool"


def _date_format_message(us_dates):
    """Return the date-format reminder for the selected convention."""
    if us_dates:
        return (
            "[yellow]Note: interpreting dates as US format "
            "month-day-year (MM-DD-YYYY).[/yellow]"
        )
    return (
        "[yellow]Warning: interpreting dates as European format "
        "day-month-year (DD-MM-YYYY). Add --us for the US format "
        "month-day-year (MM-DD-YYYY).[/yellow]"
    )


def _warn_date_format_once(us_dates):
    """Show the date-format reminder only the first time it is ever used."""
    if os.path.exists(DATE_WARNING_FLAG):
        return
    console.print(_date_format_message(us_dates))
    try:
        open(DATE_WARNING_FLAG, "w").close()
    except OSError:
        pass


def config():
    """Sets up and parses command-line arguments for all modes."""
    parser = argparse.ArgumentParser(
        description="A powerful command-line interface for arXiv.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "-d",
        "--daily",
        action="store_true",
        help="Mode 1: Scrape the daily arXiv page.",
    )
    mode_group.add_argument(
        "-g",
        "--general",
        action="store_true",
        help="Mode 2: Search the entire arXiv database via API.",
    )
    mode_group.add_argument(
        "-m",
        "--mail",
        action="store_true",
        help="Mode 3: Parse and filter the latest arXiv email (macOS).",
    )
    mode_group.add_argument(
        "-nl",
        "--netherlands",
        action="store_true",
        help="Mode 4: Recent papers from Dutch universities via OpenAlex (all fields, not only arXiv).",
    )
    parser.add_argument(
        "--uni",
        nargs="+",
        default=["all"],
        help="Used with -nl: which institutions to include (default: all).\n"
        "Options: " + ", ".join(NL_INSTITUTIONS.keys()) + ", all",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Used with -nl: look back this many days (default: 7).",
    )
    parser.add_argument(
        "--fetch",
        type=int,
        nargs="?",
        const=1,
        help="Used with -m: Fetch the N most recent emails via AppleScript before parsing (Default: 1).",
    )
    parser.add_argument(
        "-k",
        "--keyword",
        nargs="+",
        help="Keywords to search for in title and abstract.",
    )
    parser.add_argument("-a", "--author", nargs="+", help="An author to search for.")
    parser.add_argument(
        "-c",
        "--category",
        nargs="+",
        help="arXiv categories to filter by (e.g., cs.CV, cs.AI, cs.*). Not used in mail mode.",
    )
    parser.add_argument(
        "--max", type=int, default=10, help="Maximum number of papers to return."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fetch all available results for the given query.",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        help="(General Mode Only) Start date for search.\n"
        "Default format is European: day-month-year (DD-MM-YYYY).\n"
        "Add --us to use the US format month-day-year (MM-DD-YYYY).",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        help="(General Mode Only) End date for search.\n"
        "Default format is European: day-month-year (DD-MM-YYYY).\n"
        "Add --us to use the US format month-day-year (MM-DD-YYYY).",
    )
    parser.add_argument(
        "--us",
        action="store_true",
        help="Interpret --start-date/--end-date as US format month-day-year (MM-DD-YYYY)\n"
        "instead of the default European day-month-year (DD-MM-YYYY).",
    )

    return parser.parse_args()


# ─── MODE 1: General API Scraper ─────────────────────────────────────────────


def search_general_api(
    categories=None,
    keywords=None,
    authors=None,
    start_date=None,
    end_date=None,
    us_dates=False,
    max_results=25,
):
    base_url = "http://export.arxiv.org/api/query?"
    query_parts = []
    sort_by = "relevance" if keywords else "submittedDate"

    fetch_limit = max_results

    if categories:
        cat_query = "+OR+".join([f"cat:{cat}" for cat in categories])
        query_parts.append(f"({cat_query})")

    if keywords:
        processed_keywords = [f'"{kw}"' if " " in kw else kw for kw in keywords]
        for kw in processed_keywords:
            query_parts.append(f"(ti:{kw} OR abs:{kw})")

    if authors:
        for au in authors:
            query_parts.append(f'au:"{au}"')

    if start_date or end_date:
        # Always accept the unambiguous ISO format (YYYY-MM-DD); fall back to the
        # selected day/month convention for the shorter forms.
        convention = "%m-%d-%Y" if us_dates else "%d-%m-%Y"

        def _parse_date(value):
            for fmt in ("%Y-%m-%d", convention):
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue
            raise ValueError(value)

        try:
            start = (
                _parse_date(start_date).strftime("%Y%m%d%H%M%S")
                if start_date
                else "20000101000000"
            )
            end = (
                _parse_date(end_date).strftime("%Y%m%d%H%M%S")
                if end_date
                else datetime.now().strftime("%Y%m%d%H%M%S")
            )
            query_parts.append(f"submittedDate:[{start} TO {end}]")

        except ValueError:
            # Wrong format: always remind the user of the expected convention.
            console.print(_date_format_message(us_dates))
            return pd.DataFrame()

        # Parsed fine: show the convention only the first time it is ever used.
        _warn_date_format_once(us_dates)

    if not query_parts:
        return pd.DataFrame()

    search_query = "+AND+".join(query_parts)
    full_query = f"{base_url}search_query={search_query}&sortBy={sort_by}&sortOrder=descending&max_results={fetch_limit}"
    with console.status(
        f"Searching arXiv database for '[cyan]{search_query}[/cyan]'...",
        spinner="dots",
    ):
        try:
            response = requests.get(full_query)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            results = {
                "title": [],
                "authors": [],
                "subjects": [],
                "abstract": [],
                "url": [],
            }

            for entry in feed.entries:
                results["title"].append(entry.title.replace("\n", " ").strip())
                results["authors"].append(
                    ", ".join(author.name for author in entry.authors)
                )
                results["abstract"].append(entry.summary.replace("\n", " ").strip())
                results["url"].append(entry.link)
                subjects = ", ".join(tag.term for tag in entry.tags)
                results["subjects"].append(subjects)

            df = pd.DataFrame(results)

            return df.head(max_results)

        except requests.exceptions.RequestException:
            return pd.DataFrame()


# ─── MODE 2: Scrape New Papers (daily) ───────────────────────────────────────


def scrape_daily_papers(
    categories=None, keywords=None, authors=None,
    max_papers=10,
):
    url = "https://arxiv.org/list/cs/new"
    console.print(
        f"[cyan]Scraping daily papers from [link={url}]{url}[/link]...[/cyan]"
    )
    filter_categories = set(c.lower() for c in categories) if categories else set()
    filter_keywords = [k.lower() for k in keywords] if keywords else []
    filter_authors = [a.lower() for a in authors] if authors else []
    with console.status(
        "Fetching, parsing, and ranking daily papers...", spinner="dots"
    ):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            found_papers = []
            articles_dl = soup.find("dl", id="articles")

            if not articles_dl:
                return pd.DataFrame()
            parsing_state = None

            for tag in articles_dl.children:
                if tag.name == "h3":
                    tag_text = tag.get_text()
                    if "New submissions" in tag_text:
                        parsing_state = "new"
                    elif "Cross submissions" in tag_text:
                        parsing_state = "cross"
                    elif "Replacements" in tag_text:
                        break
                if (parsing_state in ["new", "cross"]) and (tag.name == "dt"):
                    dd_tag = tag.find_next_sibling("dd")

                    if not dd_tag:
                        continue

                    title_div = dd_tag.find("div", class_="list-title")
                    authors_div = dd_tag.find("div", class_="list-authors")
                    subjects_div = dd_tag.find("div", class_="list-subjects")
                    abstract_p = dd_tag.find("p", class_="mathjax")
                    arxiv_id_link = tag.find("a", title="Abstract")

                    if not all(
                        [title_div, authors_div, subjects_div, arxiv_id_link]
                    ):
                        continue

                    paper_title = title_div.text.replace("Title:", "").strip()
                    paper_authors = authors_div.text.replace("Authors:", "").strip()
                    paper_subjects_str = subjects_div.text.replace(
                        "Subjects:", ""
                    ).strip()
                    paper_abstract = abstract_p.text.strip() if abstract_p else ""

                    if filter_categories and "cs.*" not in filter_categories:
                        paper_subjects = {
                            s.split("(")[-1].replace(")", "").strip().lower()
                            for s in paper_subjects_str.split(";")
                        }
                        if not filter_categories.intersection(paper_subjects):
                            continue

                    text_to_search = paper_title + " " + paper_abstract
                    if filter_keywords:
                        keyword_pattern = (
                            r"\b("
                            + "|".join(re.escape(kw) for kw in filter_keywords)
                            + r")\b"
                        )
                        if not re.search(
                            keyword_pattern, text_to_search, re.IGNORECASE
                        ):
                            continue

                    if filter_authors and not all(
                        au in paper_authors.lower() for au in filter_authors
                    ):
                        continue
                    arxiv_id = arxiv_id_link.text.replace("arXiv:", "").strip()
                    paper_data = {
                        "title": paper_title,
                        "authors": paper_authors,
                        "subjects": paper_subjects_str,
                        "abstract": paper_abstract,
                        "url": f"http://arxiv.org/abs/{arxiv_id}",
                    }
                    found_papers.append(paper_data)

            if not found_papers:
                return pd.DataFrame()

            if filter_keywords:
                title_weight = 3.0

                for paper in found_papers:
                    keyword_pattern = (
                        r"\b("
                        + "|".join(re.escape(kw) for kw in filter_keywords)
                        + r")\b"
                    )
                    title_matches = len(
                        re.findall(keyword_pattern, paper["title"], re.IGNORECASE)
                    )
                    abstract_matches = len(
                        re.findall(keyword_pattern, paper["abstract"], re.IGNORECASE)
                    )
                    score = (title_matches * title_weight) + abstract_matches
                    paper["relevance_score"] = score
                found_papers.sort(key=lambda p: p["relevance_score"], reverse=True)

            df = pd.DataFrame(found_papers[:max_papers])

            return df

        except Exception:
            return pd.DataFrame()


# ─── MODE 3: Email Parser ────────────────────────────────────────────────────


def parse_email_papers(
    keywords=None, authors=None,
    max_papers=10, all_papers=False, fetch_count=None,
):
    """Parses the mail_text.txt file, omitting subjects."""
    if fetch_count is not None:
        console.print(
            f"[cyan]Fetching the last [bold]{fetch_count}[/bold] emails via Mail app...[/cyan]"
        )
        try:
            result = subprocess.run(
                ["osascript", SCRIPT_FILE, str(fetch_count)],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and "Success" in result.stdout:
                console.print(f"[green]{result.stdout.strip()}[/green]")
            else:
                console.print(
                    f"[bold red]AppleScript Error:[/bold red] {result.stderr or result.stdout}"
                )
        except Exception as e:
            console.print(f"[bold red]Failed to run AppleScript: {e}[/bold red]")

    console.print(
        f"[cyan]Parsing and filtering papers from [bold]{MAIL_FILE}[/bold]...[/cyan]"
    )
    try:
        with open(MAIL_FILE, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except FileNotFoundError:
        console.print(
            f"[bold red]Error: Mail file not found at '{MAIL_FILE}'.[/bold red]"
        )
        return pd.DataFrame()

    paper_sections = content.split(
        "------------------------------------------------------------------------------"
    )
    found_papers = []

    arxiv_id_re = re.compile(r"arXiv:(\d{4}\.\d{5})")
    title_re = re.compile(r"Title:\s*(.*?)\nAuthors:", re.DOTALL)
    authors_re = re.compile(r"Authors:\s*(.*?)\n", re.DOTALL)
    abstract_re = re.compile(
        r"Comments:.*?\n\\\\\n(.*?)\n\\\\\s\(|Categories:.*?\n\\\\\n(.*?)\n\\\\\s\(",
        re.DOTALL,
    )

    for section in paper_sections:
        if "Title:" not in section:
            continue

        arxiv_id_match = arxiv_id_re.search(section)
        title_match = title_re.search(section)
        authors_match = authors_re.search(section)
        abstract_match = abstract_re.search(section)

        if all([arxiv_id_match, title_match, authors_match, abstract_match]):
            abstract_text = (
                (abstract_match.group(1) or abstract_match.group(2) or "")
                .replace("\n", " ")
                .strip()
            )

            paper_data = {
                "title": title_match.group(1).replace("\n", " ").strip(),
                "authors": authors_match.group(1).replace("\n", " ").strip(),
                "abstract": abstract_text,
                "url": f"http://arxiv.org/abs/{arxiv_id_match.group(1).strip()}",
            }
            found_papers.append(paper_data)

    df = pd.DataFrame(found_papers)
    if df.empty:
        return df

    if keywords:
        keyword_pattern = (
            r"\b(" + "|".join(re.escape(kw) for kw in keywords) + r")\b"
        )
        mask = df.apply(
            lambda row: re.search(
                keyword_pattern,
                row["title"] + " " + row["abstract"],
                re.IGNORECASE,
            )
            is not None,
            axis=1,
        )
        df = df[mask].copy()

    if authors:
        author_pattern = "|".join(re.escape(au) for au in authors)
        mask = df["authors"].str.contains(author_pattern, case=False, na=False)
        df = df[mask].copy()

    if df.empty:
        return df

    if keywords:
        title_weight = 3.0
        df["relevance_score"] = df.apply(
            lambda row: (
                len(re.findall(keyword_pattern, row["title"], re.IGNORECASE))
                * title_weight
            )
            + len(re.findall(keyword_pattern, row["abstract"], re.IGNORECASE)),
            axis=1,
        )
        df = df.sort_values(by="relevance_score", ascending=False)

    limit = len(df) if all_papers else max_papers
    df = df.head(limit)

    return df


# ─── MODE 4: Netherlands via OpenAlex ────────────────────────────────────────


def _rebuild_abstract(inverted_index):
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the text."""
    if not inverted_index:
        return ""
    positions = []
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions.append((i, word))
    return " ".join(w for _, w in sorted(positions))


def search_netherlands(
    unis=None, days=7, keywords=None, authors=None, max_papers=10, all_papers=False,
):
    """Recent works whose authors are affiliated with Dutch institutions.

    Uses OpenAlex instead of arXiv so that affiliations are resolved and
    non-arXiv fields (chemistry, materials, bio, engineering) are covered.
    """
    unis = unis or ["all"]
    if "all" in unis:
        selected = list(NL_INSTITUTIONS.keys())
    else:
        unknown = [u for u in unis if u not in NL_INSTITUTIONS]
        if unknown:
            console.print(
                f"[bold red]Unknown --uni value(s): {', '.join(unknown)}. "
                f"Choose from: {', '.join(NL_INSTITUTIONS.keys())}, all[/bold red]"
            )
            return pd.DataFrame()
        selected = unis

    ror_by_key = {k: NL_INSTITUTIONS[k][1] for k in selected}
    name_by_ror = {v[1]: v[0] for v in NL_INSTITUTIONS.values()}
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    filters = [
        "authorships.institutions.ror:" + "|".join(ror_by_key.values()),
        f"from_publication_date:{since}",
    ]
    if keywords:
        # OpenAlex full-text search over title + abstract.
        filters.append("title_and_abstract.search:" + " ".join(keywords))
    if authors:
        filters.append("raw_author_name.search:" + " ".join(authors))

    select = ",".join(
        [
            "id",
            "title",
            "publication_date",
            "authorships",
            "primary_topic",
            "doi",
            "primary_location",
            "abstract_inverted_index",
        ]
    )
    per_page = 200
    params = {
        "filter": ",".join(filters),
        "select": select,
        "per-page": per_page,
        "sort": "publication_date:desc",
        "cursor": "*",
    }
    if OPENALEX_MAILTO:
        params["mailto"] = OPENALEX_MAILTO

    limit = 10_000 if all_papers else max_papers
    found_papers = []

    uni_label = ", ".join(NL_INSTITUTIONS[k][0] for k in selected)
    console.print(
        f"[cyan]Querying OpenAlex for the last {days} days at: {uni_label}[/cyan]"
    )
    with console.status("Fetching Dutch papers from OpenAlex...", spinner="dots"):
        try:
            while len(found_papers) < limit:
                response = requests.get(
                    "https://api.openalex.org/works", params=params, timeout=30
                )
                response.raise_for_status()
                payload = response.json()
                for w in payload.get("results", []):
                    nl_affils = set()
                    author_names = []
                    for a in w.get("authorships", []):
                        author_names.append(a.get("author", {}).get("display_name", ""))
                        for inst in a.get("institutions", []):
                            if inst.get("ror") in name_by_ror:
                                nl_affils.add(name_by_ror[inst["ror"]])
                    topic = (w.get("primary_topic") or {}).get("display_name", "")
                    field = (
                        ((w.get("primary_topic") or {}).get("field") or {})
                        .get("display_name", "")
                    )
                    loc = w.get("primary_location") or {}
                    url = (
                        w.get("doi")
                        or loc.get("landing_page_url")
                        or w.get("id")
                    )
                    found_papers.append(
                        {
                            "title": (w.get("title") or "").strip(),
                            "authors": ", ".join(n for n in author_names if n),
                            "subjects": "; ".join(
                                s for s in [field, topic, " / ".join(sorted(nl_affils)),
                                            w.get("publication_date", "")] if s
                            ),
                            "abstract": _rebuild_abstract(
                                w.get("abstract_inverted_index")
                            ),
                            "url": url,
                        }
                    )
                    if len(found_papers) >= limit:
                        break
                next_cursor = (payload.get("meta") or {}).get("next_cursor")
                if not next_cursor or not payload.get("results"):
                    break
                params["cursor"] = next_cursor
        except requests.exceptions.RequestException as e:
            console.print(f"[bold red]OpenAlex request failed: {e}[/bold red]")
            return pd.DataFrame()

    return pd.DataFrame(found_papers)


# ─── Main ────────────────────────────────────────────────────────────────────


def main():
    args = config()
    df = pd.DataFrame()
    show_subjects_in_table = True

    if args.daily:
        console.print("[bold green]Mode: Daily Digest[/bold green]")
        df = scrape_daily_papers(
            categories=args.category,
            keywords=args.keyword,
            authors=args.author,
            max_papers=(9999 if args.all else args.max),
        )
    elif args.general:
        console.print("[bold green]Mode: General API Search[/bold green]")
        df = search_general_api(
            categories=args.category,
            keywords=args.keyword,
            authors=args.author,
            start_date=args.start_date,
            end_date=args.end_date,
            us_dates=args.us,
            max_results=(2000 if args.all else args.max),
        )
    elif args.mail:
        console.print("[bold green]Mode: Email Parser[/bold green]")
        df = parse_email_papers(
            keywords=args.keyword,
            authors=args.author,
            max_papers=args.max,
            all_papers=args.all,
            fetch_count=args.fetch,
        )
        show_subjects_in_table = False
    elif args.netherlands:
        console.print("[bold green]Mode: Netherlands (OpenAlex)[/bold green]")
        df = search_netherlands(
            unis=args.uni,
            days=args.days,
            keywords=args.keyword,
            authors=args.author,
            max_papers=args.max,
            all_papers=args.all,
        )

    if df.empty:
        console.print("[yellow]No matching papers found.[/yellow]")
        return

    keywords_to_highlight = []
    if args.keyword:
        for phrase in args.keyword:
            keywords_to_highlight.extend(phrase.split())
    if args.author:
        for name in args.author:
            keywords_to_highlight.extend(name.split())

    table = Table(box=box.HORIZONTALS, show_lines=False, show_header=True)
    table.add_column(
        "Search Terms", max_width=20, style=Style(color="cyan", bold=True)
    )
    table.add_column("Paper Details")

    if keywords_to_highlight:
        terms_to_display = (args.keyword or []) + (args.author or [])
        table.add_row(
            Text(
                f"Highlighting for:\n{', '.join(terms_to_display)}", style="yellow"
            )
        )
        table.add_section()

    table = add_to_table(
        df,
        table,
        keywords_to_highlight,
        show_subjects=show_subjects_in_table,
    )
    console.print(table)


if __name__ == "__main__":
    main()