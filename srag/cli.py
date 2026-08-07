# srag/cli.py
from __future__ import annotations
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="srag", help="RAG document Q&A for civil society organizations", add_completion=False)
console = Console(highlight=False)


def _cfg():
    from srag.program import reload_config_from_program
    reload_config_from_program()  # PROGRAM.md is the single source of truth for models/top_k/trusted
    from srag.config import load_config
    return load_config()


@app.command()
def add(
    path: Optional[Path] = typer.Argument(None, help="File or directory to ingest"),
    url: Optional[str] = typer.Option(None, "--url", help="URL to scrape and ingest"),
    watch: Optional[Path] = typer.Option(None, "--watch", help="Directory to watch"),
):
    """Ingest a file, directory, or URL into the knowledge base."""
    from srag.store.db import init_db
    cfg = _cfg()
    init_db(cfg.db_path)

    if url:
        _ingest_url(url, cfg)
    elif watch:
        _watch_directory(watch, cfg)
    elif path:
        if path.is_dir():
            files = list(path.rglob("*"))
            files = [f for f in files if f.is_file()]
            console.print(f"Found {len(files)} files in {path}")
            for f in files:
                _ingest_file(f, cfg)
        else:
            _ingest_file(path, cfg)
    else:
        console.print("[red]Provide a path, --url, or --watch[/red]")
        raise typer.Exit(1)


def _ingest_file(path: Path, cfg, force: bool = False):
    from srag.program import ingest_path_allowed

    if not ingest_path_allowed(str(path)):
        console.print(f"[red]Blocked by PROGRAM.md:[/red] {path} is not in the ingest path whitelist")
        return
    from srag.ingestion.parsers import parse_file
    from srag.ingestion.chunker import chunk_text
    from srag.ingestion.embedder import embed_texts
    from srag.store.db import upsert_document, insert_chunks, delete_document, doc_id, get_document
    from srag.store.models import Document, Chunk
    from datetime import datetime, timezone

    try:
        mtime = path.stat().st_mtime
        existing = get_document(cfg.db_path, str(path))
        if existing and existing.mtime == mtime and not force:
            console.print(f"[dim]Skipped (unchanged): {path}[/dim]")
            return

        text, title = parse_file(path)
        if cfg.redact_pii:
            from srag.ingestion.redact import redact_pii
            text = redact_pii(text)
            title = redact_pii(title)
        raw_chunks = chunk_text(text, str(path))
        embeddings = embed_texts([c.content for c in raw_chunks], cfg.embed_model)

        did = doc_id(str(path))
        if existing:
            delete_document(cfg.db_path, did)

        doc = Document(
            id=did, source_path=str(path), title=title,
            file_type=path.suffix.lstrip("."),
            ingested_at=datetime.now(timezone.utc).isoformat(),
            chunk_count=len(raw_chunks), mtime=mtime,
            embed_model=cfg.embed_model,
        )
        upsert_document(cfg.db_path, doc)

        chunks = [
            Chunk(id=None, doc_id=did, content=rc.content, chunk_index=rc.chunk_index,
                  metadata={"page": rc.page_number, "heading": rc.section_heading,
                             "source_path": str(path)})
            for rc in raw_chunks
        ]
        insert_chunks(cfg.db_path, chunks, embeddings)
        console.print(f"[green]Ingested:[/green] {path} ({len(chunks)} chunks)")
        from srag.audit import log_event
        log_event(cfg.db_path, "ingest", source=str(path), kind="file")

    except Exception as e:
        console.print(f"[red]Error ingesting {path}:[/red] {e}")


def _ingest_url(url: str, cfg):
    from srag.program import ingest_url_allowed

    if not ingest_url_allowed(url):
        console.print(f"[red]Blocked by PROGRAM.md:[/red] {url} is not in the ingest URL whitelist")
        return
    from srag.ingestion.parsers import parse_url
    from srag.ingestion.chunker import chunk_text
    from srag.ingestion.embedder import embed_texts
    from srag.store.db import upsert_document, insert_chunks, delete_document, doc_id, get_document
    from srag.store.models import Document, Chunk
    from datetime import datetime, timezone
    import time

    try:
        text, title = parse_url(url)
        if cfg.redact_pii:
            from srag.ingestion.redact import redact_pii
            text = redact_pii(text)
            title = redact_pii(title)
        raw_chunks = chunk_text(text, url)
        embeddings = embed_texts([c.content for c in raw_chunks], cfg.embed_model)

        did = doc_id(url)
        if get_document(cfg.db_path, url):
            delete_document(cfg.db_path, did)

        doc = Document(
            id=did, source_path=url, title=title, file_type="url",
            ingested_at=datetime.now(timezone.utc).isoformat(),
            chunk_count=len(raw_chunks), mtime=time.time(),
            embed_model=cfg.embed_model,
        )
        upsert_document(cfg.db_path, doc)
        chunks = [
            Chunk(id=None, doc_id=did, content=rc.content, chunk_index=rc.chunk_index,
                  metadata={"page": rc.page_number, "heading": rc.section_heading,
                             "source_path": url})
            for rc in raw_chunks
        ]
        insert_chunks(cfg.db_path, chunks, embeddings)
        console.print(f"[green]Ingested URL:[/green] {title} ({len(chunks)} chunks)")
        from srag.audit import log_event
        log_event(cfg.db_path, "ingest", source=url, kind="url")

    except Exception as e:
        console.print(f"[red]Error ingesting {url}:[/red] {e}")


def _watch_directory(path: Path, cfg):
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    import time

    class Handler(FileSystemEventHandler):
        def on_modified(self, event):
            if not event.is_directory:
                _ingest_file(Path(event.src_path), cfg)

        def on_created(self, event):
            if not event.is_directory:
                _ingest_file(Path(event.src_path), cfg)

    observer = Observer()
    observer.schedule(Handler(), str(path), recursive=True)
    observer.start()
    console.print(f"[green]Watching:[/green] {path}  (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


@app.command()
def query(
    question: str = typer.Argument(..., help="Question to ask"),
    k: int = typer.Option(0, "-k", help="Top-k results (0 = use config default)"),
    no_run: bool = typer.Option(False, "--no-run", help="Disable command execution"),
):
    """Ask a question. Streams the answer with citations."""
    from srag.agent.loop import run_agent
    from srag.audit import log_event
    from srag.store.db import init_db, save_query_context, stale_embed_model_count

    cfg = _cfg()
    if k > 0:
        cfg.top_k = k
    init_db(cfg.db_path)

    stale = stale_embed_model_count(cfg.db_path, cfg.embed_model)
    if stale:
        console.print(
            f"[yellow]Warning:[/yellow] {stale} document(s) were embedded with a "
            f"different model than the current embed_model ({cfg.embed_model}) and "
            f"are excluded from search results until you run [bold]srag reindex[/bold]."
        )

    def confirm(cmd: str) -> bool:
        if no_run:
            return False
        console.print(f"\n[bold yellow]Proposed command:[/bold yellow] {cmd}")
        answer = console.input("Run this? [[y]/N] ").strip().lower()
        return answer == "y"

    chunk_collector: list = []
    qid_holder: list = []
    full_response = ""
    for token in run_agent(question, cfg, confirm_fn=confirm,
                           collected_chunks=chunk_collector,
                           query_id_holder=qid_holder):
        console.print(token, end="")
        full_response += token
    console.print()
    if qid_holder:
        save_query_context(cfg.db_path, qid_holder[0], question, chunk_collector)
        log_event(cfg.db_path, "query",
                  query_id=qid_holder[0],
                  question=question,
                  chunk_ids=chunk_collector,
                  source_count=len(chunk_collector))


@app.command(name="list")
def list_docs():
    """List all ingested documents."""
    from srag.store.db import list_documents, init_db

    cfg = _cfg()
    init_db(cfg.db_path)
    docs = list_documents(cfg.db_path)

    if not docs:
        console.print("[dim]No documents ingested yet.[/dim]")
        return

    table = Table(title="Knowledge Base Documents")
    table.add_column("ID", style="dim", max_width=16)
    table.add_column("Title")
    table.add_column("Type")
    table.add_column("Chunks", justify="right")
    table.add_column("Embed Model")
    table.add_column("Ingested At")
    for doc in docs:
        table.add_row(doc.id, doc.title or doc.source_path, doc.file_type,
                      str(doc.chunk_count), doc.embed_model or "[dim]—[/dim]",
                      doc.ingested_at[:19])
    console.print(table)


@app.command()
def delete(doc_id: str = typer.Argument(..., help="Document ID to remove")):
    """Remove a document and all its chunks."""
    from srag.store.db import delete_document, init_db

    cfg = _cfg()
    init_db(cfg.db_path)
    delete_document(cfg.db_path, doc_id)
    console.print(f"[green]Deleted:[/green] {doc_id}")


@app.command()
def reindex():
    """Re-embed all documents (use after changing the embedding model)."""
    from srag.store.db import list_documents, init_db

    cfg = _cfg()
    init_db(cfg.db_path)
    docs = list_documents(cfg.db_path)
    console.print(f"Re-indexing {len(docs)} documents…")
    for doc in docs:
        if doc.file_type == "url":
            _ingest_url(doc.source_path, cfg)
        else:
            path = Path(doc.source_path)
            if not path.exists():
                console.print(f"[yellow]Skipped (source missing):[/yellow] {doc.source_path}")
                continue
            _ingest_file(path, cfg, force=True)
    console.print("[green]Reindex complete.[/green]")


@app.command()
def serve(port: int = typer.Option(8000, "--port", help="Port to listen on"),
          host: str = typer.Option("127.0.0.1", "--host", help="Host/interface to bind")):
    """Start the web UI server."""
    import uvicorn
    from srag.api.app import app as fastapi_app

    console.print(f"[green]Starting web UI at http://{host}:{port}[/green]")
    uvicorn.run(fastapi_app, host=host, port=port)


@app.command()
def models():
    """List available Ollama models."""
    import ollama

    try:
        resp = ollama.list()
        table = Table(title="Available Ollama Models")
        table.add_column("Name")
        table.add_column("Size")
        for m in resp.get("models", []):
            size_gb = m.get("size", 0) / 1e9
            table.add_row(m["name"], f"{size_gb:.1f} GB")
        console.print(table)
    except Exception as e:
        console.print(f"[red]Cannot reach Ollama:[/red] {e}\nRun: ollama serve")


@app.command()
def config(
    action: str = typer.Argument("show", help="show | set"),
    key: Optional[str] = typer.Argument(None),
    value: Optional[str] = typer.Argument(None),
):
    """Show or update configuration values."""
    from srag.config import load_config, save_config
    from dataclasses import asdict

    cfg = load_config()
    if action == "show":
        for k, v in asdict(cfg).items():
            if k == "tavily_api_key" and v:
                v = v[:8] + "…" if len(v) > 8 else "***"
            console.print(f"  {k} = {v}")
    elif action == "set":
        if not key or value is None:
            console.print("[red]Usage: srag config set <key> <value>[/red]")
            raise typer.Exit(1)
        d = asdict(cfg)
        if key not in d:
            console.print(f"[red]Unknown config key: {key}[/red]")
            raise typer.Exit(1)
        # Coerce to the original type
        original_type = type(d[key])
        if original_type is bool:
            d[key] = str(value).strip().lower() in ("1", "true", "yes", "on")
        else:
            d[key] = original_type(value)
        from srag.config import Config
        save_config(Config(**d))
        console.print(f"[green]Set {key} = {value}[/green]")


@app.command()
def note(body: str = typer.Argument(..., help="Note text to record")):
    """Record a post-incident note, auto-linked to the most recent query."""
    from srag.store.db import init_db, save_note

    cfg = _cfg()
    init_db(cfg.db_path)
    ctx = save_note(cfg.db_path, body)
    if ctx:
        console.print(
            f"[green]Note saved[/green] (linked to query: {ctx['question'][:50]})"
        )
    else:
        console.print("[green]Note saved[/green] (no recent query to link)")


@app.command(name="notes")
def list_notes_cmd():
    """List all incident notes with linked queries."""
    from srag.store.db import init_db, list_notes

    cfg = _cfg()
    init_db(cfg.db_path)
    notes = list_notes(cfg.db_path)

    if not notes:
        console.print("[dim]No notes yet. Use [bold]srag note[/bold] to record one.[/dim]")
        return

    table = Table(title="Incident Notes")
    table.add_column("ID", style="dim", max_width=6)
    table.add_column("Note", max_width=50)
    table.add_column("Linked Query", max_width=40)
    table.add_column("Created", max_width=20)
    for n in notes:
        linked = n["linked_question"] or "[dim]—[/dim]"
        table.add_row(
            str(n["id"]),
            n["body"][:47] + ("..." if len(n["body"]) > 47 else ""),
            linked[:37] + ("..." if len(linked) > 37 else ""),
            n["created_at"][:19],
        )
    console.print(table)
