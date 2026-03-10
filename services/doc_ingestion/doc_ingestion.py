"""Entry point for the document ingestion service.

Configuration is read from environment variables (or .doc_ingestion.env).

Per-engine (required to activate an engine):
    DOC_INGESTION_{ENGINE}_PATH      Directory to scan, e.g. DOC_INGESTION_PAPERLESS_PATH
    DOC_INGESTION_{ENGINE}_TEMPLATE  Path template (optional, falls back to global)
    DOC_INGESTION_{ENGINE}_OWNER_ID  DMS owner_id (optional, falls back to global)

Global fallbacks:
    DOC_INGESTION_OWNER_ID           Default owner_id
    DOC_INGESTION_WATCH              Watch mode for all engines: 'true' / '1' (default: false)
"""
import asyncio
import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

from shared.logging.logging_setup import setup_logging
from shared.helper.HelperConfig import HelperConfig
from shared.clients.cache.CacheClientManager import CacheClientManager
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.dms.DMSClientManager import DMSClientManager
from shared.clients.llm.LLMClientManager import LLMClientManager
from shared.clients.ocr.OCRClientManager import OCRClientManager
from services.doc_ingestion.IngestionService import IngestionService
from services.doc_ingestion.helper.FileScanner import FileScanner
from shared.clients.prompt.PromptClientManager import PromptClientManager
from shared.clients.prompt.PromptClientInterface import PromptClientInterface

load_dotenv()
logging = setup_logging()

_DEFAULT_TEMPLATE = "{filename}"


@dataclass
class _EngineTask:
    dms_client: DMSClientInterface
    engine_name: str
    path: str
    template: str
    owner_id: int | None


def _read_engine_tasks(dms_clients: list[DMSClientInterface]) -> list[_EngineTask]:
    """Build per-engine ingestion tasks from environment variables.

    An engine is activated when DOC_INGESTION_{ENGINE}_PATH is set.
    Template and owner_id fall back to global defaults if not set per-engine.
    """
    global_owner_raw = os.getenv("DOC_INGESTION_OWNER_ID", "").strip()
    global_owner_id = int(global_owner_raw) if global_owner_raw else None

    tasks: list[_EngineTask] = []
    for client in dms_clients:
        engine = client.get_engine_name().upper()

        path = os.getenv("DOC_INGESTION_%s_PATH" % engine, "").strip()
        if not path:
            logging.debug(
                "Engine '%s': DOC_INGESTION_%s_PATH not set, skipping.", engine, engine
            )
            continue

        if not os.path.isdir(path):
            logging.error(
                "Engine '%s': path '%s' does not exist or is not a directory. Skipping engine.",
                engine, path,
            )
            continue
        
        # Read the path template for engine
        template_name = "DOC_INGESTION_%s_TEMPLATE" % engine
        #if the env is not set, throw error
        if template_name not in os.environ:
            raise ValueError(f"Missing required environment variable '{template_name}' for engine '{engine}'.")
        template = os.environ[template_name].strip()
        #if template is empty, throw error
        if not template:
            raise ValueError(f"Environment variable '{template_name}' for engine '{engine}' cannot be empty.")

        owner_raw = os.getenv("DOC_INGESTION_%s_OWNER_ID" % engine, "").strip()
        owner_id = int(owner_raw) if owner_raw else global_owner_id

        tasks.append(
            _EngineTask(
                dms_client=client,
                engine_name=client.get_engine_name(),
                path=path,
                template=template,
                owner_id=owner_id,
            )
        )
        logging.info(
            "Engine '%s': path='%s', template='%s', owner_id=%s",
            engine, path, template, owner_id,
        )

    return tasks


async def _run_once(tasks: list[_EngineTask], helper_config: HelperConfig, llm_client, cache_client, ocr_client, prompt_client: PromptClientInterface|None = None) -> None:
    """Scan each engine's directory once and ingest all found files in batches.

    Files are processed phase-by-phase within each batch so each LLM model
    stays resident in VRAM for the full batch before being swapped out.
    Batch size is controlled by ``DOC_INGESTION_BATCH_SIZE`` (0 = no limit).
    """
    batch_size = int(os.getenv("DOC_INGESTION_BATCH_SIZE", "0").strip())
    for task in tasks:
        service = IngestionService(
            helper_config=helper_config,
            dms_client=task.dms_client,
            llm_client=llm_client,
            cache_client=cache_client,
            template=task.template,
            default_owner_id=task.owner_id,
            ocr_client=ocr_client,
            prompt_client=prompt_client,
        )
        scanner = FileScanner(root_path=task.path)
        files = scanner.scan_once()
        logging.info(
            "Engine '%s': found %d file(s) in '%s'.",
            task.engine_name, len(files), task.path,
        )
        await service.do_ingest_files_batch(file_paths=files, root_path=task.path, batch_size=batch_size)


async def _run_watch(tasks: list[_EngineTask], helper_config: HelperConfig, llm_client, cache_client, ocr_client, prompt_client: PromptClientInterface|None = None) -> None:
    """Watch each engine's directory concurrently and ingest on changes.

    Detected files are placed into a per-engine queue after their size has
    stabilised.  A single worker drains the queue in batches (controlled by
    ``DOC_INGESTION_BATCH_SIZE``) and calls ``do_ingest_files_batch`` so that
    only one batch is active at a time while the queue keeps accumulating.
    """
    batch_size = int(os.getenv("DOC_INGESTION_BATCH_SIZE", "0").strip())

    async def watch_engine(task: _EngineTask) -> None:
        service = IngestionService(
            helper_config=helper_config,
            dms_client=task.dms_client,
            llm_client=llm_client,
            cache_client=cache_client,
            template=task.template,
            default_owner_id=task.owner_id,
            ocr_client=ocr_client,
            prompt_client=prompt_client,
        )
        scanner = FileScanner(root_path=task.path)
        queue: asyncio.Queue[str] = asyncio.Queue()
        queued: set[str] = set()

        logging.info(
            "Engine '%s': starting watch mode on '%s'...", task.engine_name, task.path
        )

        async def _worker() -> None:
            while True:
                file_path = await queue.get()
                batch = [file_path]
                queued.discard(file_path)
                # drain as many immediately available files as batch_size allows
                while batch_size == 0 or len(batch) < batch_size:
                    try:
                        next_file = queue.get_nowait()
                        batch.append(next_file)
                        queued.discard(next_file)
                    except asyncio.QueueEmpty:
                        break
                logging.info(
                    "Engine '%s': processing batch of %d file(s).", task.engine_name, len(batch)
                )
                await service.do_ingest_files_batch(file_paths=batch, root_path=task.path)
                for _ in batch:
                    queue.task_done()

        asyncio.ensure_future(_worker())

        async def on_file_stable(file_path: str) -> None:
            if file_path not in queued:
                queued.add(file_path)
                await queue.put(file_path)

        await scanner.watch(on_file_stable)

    await asyncio.gather(*[watch_engine(t) for t in tasks])


async def run() -> None:
    watch = os.getenv("DOC_INGESTION_WATCH", "false").strip().lower() in ("1", "true", "yes")
    helper_config = HelperConfig(logger=logging)

    dms_clients = DMSClientManager(helper_config=helper_config).get_clients()
    llm_client = LLMClientManager(helper_config=helper_config).get_client()
    cache_client = CacheClientManager(helper_config=helper_config).get_client()
    ocr_client = OCRClientManager(helper_config=helper_config).get_client()
    prompt_client = PromptClientManager(helper_config=helper_config).get_client()

    # boot regular clients
    for client in [*dms_clients, llm_client, cache_client, ocr_client]:
        await client.boot()
        await client.do_healthcheck()

    # boot prompt client. If this fails note the user and continue
    try:
        await prompt_client.boot()
        await prompt_client.do_healthcheck()
    except Exception as e:
        logging.error("Failed to boot prompt client: %s. Prompt-based features will be using local fallbacks. Error: %s", prompt_client._get_engine_name(), e)
        prompt_client = None

    tasks = _read_engine_tasks(dms_clients)
    if not tasks:
        logging.error(
            "No engines configured for ingestion. "
            "Set DOC_INGESTION_{ENGINE}_PATH for at least one engine "
            "and ensure the directory exists."
        )
        sys.exit(0)

    for task in tasks:
        await task.dms_client.fill_cache()

    if watch:
        await _run_watch(tasks, helper_config, llm_client, cache_client, ocr_client, prompt_client)
    else:
        await _run_once(tasks, helper_config, llm_client, cache_client, ocr_client, prompt_client)

    for client in [*dms_clients, llm_client, cache_client, ocr_client]:
        await client.close()
    # close if it was booted successfully
    if prompt_client:
        await prompt_client.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
