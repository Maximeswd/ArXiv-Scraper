import requests
import feedparser
import argparse
import pandas as pd
import re
import subprocess
from bs4 import BeautifulSoup
from datetime import datetime
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
        help="(General Mode Only) Start date for search (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        help="(General Mode Only) End date for search (YYYY-MM-DD).",
    )

    return parser.parse_args()


# MODE 1: General API Scraper
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
            return pd.DataFrame()
    
    if not query_parts:
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
        
        except requests.exceptions.RequestException:
            return pd.DataFrame()


# MODE 2: Scrape New Papers (daily)
def scrape_daily_papers(categories=None, keywords=None, authors=None, max_papers=10):
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
            
            return pd.DataFrame(found_papers[:max_papers])
        
        except Exception:
            return pd.DataFrame()


# MODE 3: Email Parser
def parse_email_papers(keywords=None, authors=None, max_papers=10, all_papers=False, fetch_count=None):
    """Parses the mail_text.txt file, omitting subjects."""
    if fetch_count is not None:
        console.print(f"[cyan]Fetching the last [bold]{fetch_count}[/bold] emails via Mail app...[/cyan]")
        try:
            result = subprocess.run(
                ["osascript", SCRIPT_FILE, str(fetch_count)], 
                capture_output=True, text=True
            )
            if result.returncode == 0 and "Success" in result.stdout:
                console.print(f"[green]{result.stdout.strip()}[/green]")
            else:
                console.print(f"[bold red]AppleScript Error:[/bold red] {result.stderr or result.stdout}")
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
        keyword_pattern = r"\b(" + "|".join(re.escape(kw) for kw in keywords) + r")\b"
        mask = df.apply(
            lambda row: re.search(
                keyword_pattern, row["title"] + " " + row["abstract"], re.IGNORECASE
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
    return df.head(limit)


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
            max_results=(2000 if args.all else args.max),
        )
    elif args.mail:
        console.print("[bold green]Mode: Email Parser[/bold green]")
        df = parse_email_papers(
            keywords=args.keyword,
            authors=args.author,
            max_papers=args.max,
            all_papers=args.all,
            fetch_count=args.fetch 
        )
        show_subjects_in_table = False

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
    table.add_column("Search Terms", max_width=20, style=Style(color="cyan", bold=True))
    table.add_column("Paper Details")

    if keywords_to_highlight:
        terms_to_display = (args.keyword or []) + (args.author or [])
        table.add_row(
            Text(f"Highlighting for:\n{', '.join(terms_to_display)}", style="yellow")
        )
        table.add_section()

    table = add_to_table(
        df, table, keywords_to_highlight, show_subjects=show_subjects_in_table
    )
    console.print(table)


if __name__ == "__main__":
    main()
