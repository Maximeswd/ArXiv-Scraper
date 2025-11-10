import requests
import feedparser
import argparse
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.style import Style
from rich.text import Text
from rich import box
import re
from bs4 import BeautifulSoup
from datetime import datetime
from utils import add_to_table

# Setup
console = Console()


def config():
    """
    Sets up and parses command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="A powerful two-mode command-line interface for arXiv.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "-d",
        "--daily",
        action="store_true",
        help="Mode 1: Scrape the daily arXiv page (arxiv.org/list/cs/new).",
    )
    mode_group.add_argument(
        "-g",
        "--general",
        action="store_true",
        help="Mode 2: Search the entire arXiv database via the API.",
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
        help="arXiv categories to filter by (e.g., cs.CV, cs.AI, cs.*).\n"
        "If not provided, a baseline list (CV, LG, CL, AI, IR) is used.",
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
        help="(General Mode Only) Start date for search (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        help="(General Mode Only) End date for search (YYYY-MM-DD).",
    )

    return parser.parse_args()


# MODE 1: Daily Digest Scraper
def scrape_daily_papers(categories=None, keywords=None, authors=None, max_papers=10):
    """
    Scrapes the /new page, applies filters, and sorts results by a weighted
    relevance score (title matches are worth more).
    """
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
                console.print(
                    "[yellow]Could not find the main article list on the page.[/yellow]"
                )
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

                    if not all([title_div, authors_div, subjects_div, arxiv_id_link]):
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
                title_weight = 2.0
                for paper in found_papers:
                    keyword_pattern = (
                        r"\b("
                        + "|".join(re.escape(kw) for kw in filter_keywords)
                        + r")\b"
                    )

                    title_matches = len(
                        re.findall(keyword_pattern, paper["title"], re.IGNORECASE)
                    )
                    title_words = len(paper["title"].split())

                    abstract_matches = len(
                        re.findall(keyword_pattern, paper["abstract"], re.IGNORECASE)
                    )
                    abstract_words = len(paper["abstract"].split())

                    norm_title_freq = (
                        (title_matches / title_words) if title_words > 0 else 0
                    )
                    norm_abstract_freq = (
                        (abstract_matches / abstract_words) if abstract_words > 0 else 0
                    )

                    score = (norm_title_freq * title_weight) + norm_abstract_freq
                    paper["relevance_score"] = score

                found_papers.sort(key=lambda p: p["relevance_score"], reverse=True)

            return pd.DataFrame(found_papers[:max_papers])

        except Exception as e:
            console.print(
                f"[bold red]An error occurred during scraping: {e}[/bold red]"
            )
            return pd.DataFrame()


# MODE 2: General API Search
def search_general_api(
    categories=None,
    keywords=None,
    authors=None,
    start_date=None,
    end_date=None,
    max_results=25,
):
    base_url = "http://export.arxiv.org/api/query?"
    query_parts = []
    sort_by = "relevance" if keywords else "submittedDate"
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
        try:
            start = (
                datetime.strptime(start_date, "%Y-%m-%d").strftime("%Y%m%d%H%M%S")
                if start_date
                else "20000101000000"
            )
            end = (
                datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y%m%d%H%M%S")
                if end_date
                else datetime.now().strftime("%Y%m%d%H%M%S")
            )
            query_parts.append(f"submittedDate:[{start} TO {end}]")
        except ValueError:
            console.print(
                "[bold red]Error: Invalid date format. Please use YYYY-MM-DD.[/bold red]"
            )
            return pd.DataFrame()
    if not query_parts:
        console.print(
            "[bold red]Error: You must provide at least one search criteria for general search.[/bold red]"
        )
        return pd.DataFrame()
    search_query = "+AND+".join(query_parts)
    full_query = f"{base_url}search_query={search_query}&sortBy={sort_by}&sortOrder=descending&max_results={max_results}"
    with console.status(
        f"Searching arXiv database for '[cyan]{search_query}[/cyan]'...", spinner="dots"
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
            return pd.DataFrame(results)
        except requests.exceptions.RequestException as e:
            console.print(f"[bold red]Error during API query: {e}[/bold red]")
            return pd.DataFrame()


def main():
    args = config()

    df = pd.DataFrame()
    keywords_to_highlight = []

    BASELINE_CATEGORIES = ["cs.CV", "cs.LG", "cs.CL", "cs.AI", "cs.IR"]

    if not args.category:
        console.print(
            f"[italic blue3]No category specified. Using baseline: {', '.join(BASELINE_CATEGORIES)}[/italic blue3]"
        )
        args.category = BASELINE_CATEGORIES

    if args.daily:
        console.print("[bold green]Mode: Daily Digest[/bold green]")
        max_papers_to_fetch = 9999 if args.all else args.max
        df = scrape_daily_papers(
            categories=args.category,
            keywords=args.keyword,
            authors=args.author,
            max_papers=max_papers_to_fetch,
        )
    elif args.general:
        console.print("[bold green]Mode: General API Search[/bold green]")
        max_results = 2000 if args.all else args.max
        df = search_general_api(
            categories=args.category,
            keywords=args.keyword,
            authors=args.author,
            start_date=args.start_date,
            end_date=args.end_date,
            max_results=max_results,
        )

    if args.keyword:
        for phrase in args.keyword:
            keywords_to_highlight.extend(phrase.split())
    if args.author:
        for name in args.author:
            keywords_to_highlight.extend(name.split())

    if df.empty:
        console.print("[yellow]No matching papers found.[/yellow]")
        return

    table = Table(box=box.HORIZONTALS, show_lines=False, show_header=True)
    table.add_column("Search Terms", max_width=20, style=Style(color="cyan", bold=True))
    table.add_column("Paper Details")

    if keywords_to_highlight:
        terms_to_display = (args.keyword or []) + (args.author or [])
        table.add_row(
            Text(f"Highlighting for:\n{', '.join(terms_to_display)}", style="yellow1")
        )
        table.add_section()

    table = add_to_table(df, table, keywords_to_highlight)
    console.print(table)


if __name__ == "__main__":
    main()
