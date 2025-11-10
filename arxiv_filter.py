import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.style import Style
from rich import box
import argparse

from utils import add_to_table, get_until

console = Console()

parser = argparse.ArgumentParser(
    description="Filter arXiv daily emails based on keywords and authors."
)
parser.add_argument(
    "-k",
    "--keyword",
    nargs="+",
    help="A list of keywords to search for in title and abstract.",
)
parser.add_argument(
    "-a", "--author", nargs="+", help="A list of authors to search for."
)
args = parser.parse_args()

with console.status("Reading papers...", spinner="monkey"):
    # SETUP
    if args.keyword or args.author:
        console.print(
            "[yellow]Command-line arguments detected. Ignoring .txt files.[/yellow]"
        )
        keywords = args.keyword if args.keyword else []
        authors = args.author if args.author else []
    else:
        ValueError(
            "No command-line arguments provided. Please provide keywords or authors to search for in the format: -k 'keyword' and/or -a 'author1'."
        )

    with open("mail_text.txt", "r") as file:
        lines = file.readlines()

    # PROCESS
    results = {
        "title": [],
        "authors": [],
        "abstract": [],
        "url": [],
    }

    for i, line in enumerate(lines):
        if line.startswith("arXiv:"):
            id = line[6:].split(" ")[0].strip()
            results["url"].append(f"https://arxiv.org/abs/{id}")
        elif line.startswith("Title:"):
            results["title"].append(get_until(i, lines, "Authors:", n_skip=7))
            if len(results["url"]) != len(results["title"]):
                results["url"] = results["url"][:-1]
        elif line.startswith("Authors:"):
            results["authors"].append(get_until(i, lines, "Categories:", n_skip=9))
        elif line.startswith("Categories:"):
            results["abstract"].append(get_until(i, lines, "-----------------------"))
        elif line.startswith(
            "%%--%%--%%--%%--%%--%%--%%--%%--%%--%%--%%--%%--%%--%%--%%--%%--%%--%%--%%--%%"
        ):
            break

    df = pd.DataFrame(results)

    # OUTPUT
    table = Table(box=box.HORIZONTALS, show_lines=False, show_header=False)
    keyword_style = Style(color="red", bold=True)
    table.add_column(max_width=15, justify="center", style=keyword_style)
    table.add_column()

    if not keywords and not authors:
        console.print("[bold red]No keywords or authors to search for.[/bold red]")
    else:
        for keyword in keywords:
            table.add_row(keyword)
            match = df[
                df["title"].str.contains(keyword, case=False)
                | df["abstract"].str.contains(keyword, case=False)
            ]
            table = add_to_table(match, table, keyword)
            table.add_section()

        for author in authors:
            table.add_row(author)
            match = df[df["authors"].str.contains(author, case=False)]
            table = add_to_table(match, table, author)
            table.add_section()

        console.print(table)
