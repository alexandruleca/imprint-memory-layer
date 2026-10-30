"""What each setting is for, for people who do not remember: the longer
explanation and a worked example the dashboard shows under a setting's
one-line description (config_schema.SETTINGS). tests/test_config_help.py
keeps an entry for every setting.

Each explanation says what the setting controls, what changes when it does
(speed, quality, memory, what leaves the machine, what has to be redone) and
when to change it; the example is a valid value with the situation it fits.
Settings no code reads say so.
"""

HELP: dict[str, dict[str, str]] = {
    # ── Embeddings ───────────────────────────────────────────────────────
    "model.name": {
        "help": "The Hugging Face repository that holds the embedding model, the model that turns text into the vectors used for search. The default is Qwen's own GGUF (a quantized model file) of Qwen3-Embedding-0.6B. Changing it changes the embedding profile: an existing workspace then refuses to load with an \"Embedding profile mismatch\" error, so you need a new workspace and must reindex your sources. Other repositories usually also need model.file, and may need model.dim and model.pooling.",
        "example": "\"Qwen/Qwen3-Embedding-0.6B-GGUF\", the default: reset to it after trying another model, then switch to a fresh workspace and reindex.",
    },
    "model.revision": {
        "help": "The Git revision (commit or branch) of the model repository to download. \"auto\" uses the pinned, checksum-verified commit for the default model, and \"main\" for any other repository. The revision is part of the embedding profile, so changing it means a new workspace and a reindex. Set it only to pin a custom model to an exact commit.",
        "example": "\"370f27d7550e0def9b39c1f16d3fbaa13aa67728\": pins a custom model repository to a known commit, so a new upload to main cannot change your vectors.",
    },
    "model.file": {
        "help": "Which GGUF file inside the model repository to load. \"auto\" picks the Q8_0 file of the default model; a custom repository has no automatic choice, so name a file there. The file is part of the embedding profile, so changing it, even to another quantization of the same model, means a new workspace and a reindex.",
        "example": "\"Qwen3-Embedding-0.6B-Q8_0.gguf\": the file to name when model.name points at a repository with several quantizations.",
    },
    "model.device": {
        "help": "Where embeddings are computed. \"auto\" uses a GPU when llama.cpp finds one (Vulkan on Windows and Linux, Metal on macOS) and the CPU otherwise; \"cpu\" always uses the CPU; \"gpu\" asks for the GPU and falls back to the CPU with a warning. A GPU makes indexing much faster. Changing it does not invalidate your index, but running processes pick it up after a restart. GPU and CPU vectors are close but not identical: reindex with --force if you need perfectly consistent ranking.",
        "example": "\"cpu\": on a laptop without a supported GPU, or to keep the GPU free for games or other work.",
    },
    "model.dim": {
        "help": "The length of each embedding vector. Qwen3 models can be cut to any size from 32 up to their full width (1024 by default): smaller vectors take less disk and memory but rank slightly worse. Other models must use exactly their own width. The dimension is fixed when a collection is created, so changing it means a new workspace and a reindex.",
        "example": "512: on a machine with very little disk or RAM, set before you build a new workspace.",
    },
    "model.seq_length": {
        "help": "The most tokens (word pieces) the embedding model reads from one chunk or query; anything longer is cut off at the end before embedding. A larger value covers longer chunks but uses more memory and time per call; the default 2048 fits the default chunk sizes. It is part of the embedding profile, so changing it means a new workspace and a reindex.",
        "example": "1024: on a low-memory machine, together with smaller chunker sizes, in a new workspace.",
    },
    "model.threads": {
        "help": "How many CPU threads local models use when they run on the CPU: CPU embedding and the privacy filter. 0 uses half the logical CPUs. More threads speed up CPU embedding; fewer leave more of the machine for other work. It does not affect stored memories.",
        "example": "4: on an 8-core laptop, to keep indexing from slowing down your editor and builds.",
    },
    "model.gpu_device": {
        "help": "Which GPU runs the local models (embeddings and the local LLM) when there are several. \"auto\" takes the discrete GPU with the most memory, and an integrated GPU only when there is no discrete one; a number picks a GPU from the list Settings shows. Each model runs whole on one GPU, so a slow card never holds back a fast one.",
        "example": "\"1\": on a desktop with two GPUs, to run Imprint on the second card and leave the first free for games or training.",
    },
    "model.batch_size": {
        "help": "How many texts go into one embedding call. 0 means automatic: 8 on a GPU, which processes them together, and 1 on the CPU, so progress updates after every text. Larger batches can speed up GPU indexing but use more GPU memory. The vectors do not change, so no reindex is needed.",
        "example": "16: on a GPU with plenty of free memory, to index a large codebase faster.",
    },
    "model.pooling": {
        "help": "How the model's per-token outputs are combined into one vector. \"auto\" uses the last token for Qwen3, the CLS (first, summary) token for BGE-M3, and the model file's own setting for other models; \"cls\", \"mean\" and \"last\" force a method. The wrong choice gives poor search results. It is part of the embedding profile, so changing it means a new workspace and a reindex. Leave it on auto unless a custom model's documentation says otherwise.",
        "example": "\"mean\": for a custom embedding model whose model card says to use mean pooling and whose GGUF lacks that metadata.",
    },
    "model.query_instruction": {
        "help": "The task description placed before search queries (as \"Instruct: ... Query: ...\") so the model knows what kind of match to look for; documents never get it. \"auto\" uses Imprint's built-in instruction for Qwen3 models (find relevant code, documentation and engineering decisions) and none for other models. It is part of the embedding profile, so changing it means a new workspace and a reindex.",
        "example": "\"Retrieve meeting notes and decisions relevant to the query\": for a workspace of mostly notes and transcripts rather than code, set before you build that workspace.",
    },
    # ── Hugging Face ─────────────────────────────────────────────────────
    "huggingface.token": {
        "help": "A Hugging Face access token for model downloads: the embedding model, the local LLM, the privacy filter and the audio models. The default models are public and need none; you need one for gated or private repositories, or to avoid anonymous rate limits. When it is empty, Imprint uses your `hf auth login` credentials, and an HF_TOKEN environment variable overrides the stored value.",
        "example": "\"hf_xxxxxxxxxxxxxxxxxxxx\": a read-only token from huggingface.co/settings/tokens, when a model download fails with a 401 or 403 error.",
    },
    # ── Vector store ─────────────────────────────────────────────────────
    "qdrant.host": {
        "help": "The address of the Qdrant server, the vector database that holds your memories. By default Imprint starts and manages its own Qdrant on 127.0.0.1, and that managed server only runs on a loopback address (127.0.0.1, ::1 or localhost). To use a remote or self-managed server, set its address here and turn on qdrant.no_spawn.",
        "example": "\"qdrant.internal.example.com\" with qdrant.no_spawn on: to use a Qdrant server your team already runs and secures.",
    },
    "qdrant.port": {
        "help": "The HTTP port Imprint uses to reach Qdrant, and the port its managed Qdrant listens on. Change it only when another program already uses 6333 or your own server listens elsewhere, then restart Imprint's processes.",
        "example": "6433: when another project's Qdrant container already uses 6333 on this machine.",
    },
    "qdrant.grpc_port": {
        "help": "The gRPC port (a second network interface) that Imprint's managed Qdrant listens on. Imprint itself connects over HTTP, so this only matters to avoid a port conflict when Imprint starts its own Qdrant.",
        "example": "6434: when another Qdrant on this machine already uses 6334.",
    },
    "qdrant.version": {
        "help": "The Qdrant release Imprint downloads when it starts its own server and none is installed yet. Downloads are checked against checksums that ship with Imprint, so a version not pinned there fails to download; an already downloaded binary is reused whatever this says. Leave it at the default and let Imprint updates change it.",
        "example": "\"v1.17.1\", the default: reset to it if a changed value causes \"No pinned checksum\" errors.",
    },
    "qdrant.no_spawn": {
        "help": "When on, Imprint never starts its own Qdrant: it connects to qdrant.host and qdrant.port and reports an error if nothing answers. Turn it on when you run Qdrant yourself, for example in Docker or on another machine; leave it off for the normal setup, where Imprint downloads and runs Qdrant for you.",
        "example": "true: when you run Qdrant in Docker (`docker run -p 6333:6333 qdrant/qdrant`) and want Imprint to use that container.",
    },
    # ── Chunking ─────────────────────────────────────────────────────────
    "chunker.overlap": {
        "help": "How many characters each chunk repeats from the end of the previous one, so text near a split is still found by search; it is limited to a third of the chunk size. More overlap helps recall at chunk edges but stores and embeds more duplicated text. The next index or refresh re-chunks and re-embeds affected sources; the workspace stays compatible.",
        "example": "200: to reduce index size and indexing time on a very large repository.",
    },
    "chunker.size_code": {
        "help": "The target chunk size, in characters, for recognized code files; chunks split at blank lines or line ends near this size. Smaller chunks give more precise search hits but more chunks to embed and store; larger ones keep more of a function together. The next index or refresh re-chunks and re-embeds code sources; the workspace stays compatible.",
        "example": "2500: for a codebase of many small functions, so results point at narrower snippets.",
    },
    "chunker.size_prose": {
        "help": "The target chunk size, in characters, for everything that is not code: documents, notes and conversations. Smaller chunks make matches more specific; larger ones keep more context in each result. The next index or refresh re-chunks and re-embeds those sources; the workspace stays compatible. Keep chunks well within model.seq_length tokens, or their ends are cut off before embedding.",
        "example": "4000: if conversation results feel too long and mix several topics.",
    },
    "chunker.hard_max": {
        "help": "The largest a chunk may be, in characters, including the file-name prefix Imprint adds for context; it caps the code and prose sizes. The next index or refresh re-chunks affected sources. Keep it above both target sizes and small enough that a chunk fits model.seq_length tokens.",
        "example": "6000: after lowering model.seq_length to 1024, so no chunk is longer than the model reads.",
    },
    "chunker.semantic_threshold": {
        "help": "The topic-shift threshold of an older semantic prose splitter (lower values split more sharply). Indexing no longer uses that splitter, so this setting currently has no effect.",
        "example": "0.5, the default: there is no reason to change it.",
    },
    # ── Tagging ──────────────────────────────────────────────────────────
    "tagger.zero_shot": {
        "help": "Zero-shot topic tagging labels each new chunk with up to three topics by comparing its embedding with built-in topic descriptions. It needs no LLM, download or network, and costs little time. Turned off, newly ingested chunks get no topics unless LLM tagging adds them; tags already stored stay as they are.",
        "example": "false: on a large code-only index where you never filter or browse by topic.",
    },
    "tagger.llm": {
        "help": "LLM tagging has a language model give each memory a type, domains and topics, replacing the keyword domains and zero-shot topics. Ingestion does not wait for it: new memories are stored with the fast tags, marked pending, and a background job then tags them, newest first. Existing memories are only re-tagged by `imprint retag`. Which model runs, and whether text leaves the machine, is set by tagger.llm_provider.",
        "example": "true with tagger.llm_provider \"local\": richer topic labels without sending anything off the machine.",
    },
    "tagger.llm_provider": {
        "help": "Which model does LLM tagging. \"local\" runs the bundled helper model on this computer through llama.cpp: no API key, and no network after its one-time download. \"ollama\" and \"vllm\" call a server you run, on localhost by default. \"anthropic\", \"openai\" and \"gemini\" send each chunk's text, with secrets redacted first, to that company and need ANTHROPIC_API_KEY, OPENAI_API_KEY or GOOGLE_API_KEY. Nothing happens until tagger.llm is on. recall.llm uses this provider too, but only when it is local or a loopback ollama/vllm server.",
        "example": "\"ollama\": to tag with a model you already serve through Ollama on http://localhost:11434.",
    },
    "tagger.llm_model": {
        "help": "The model name to request from a server provider. Empty uses each provider's default: claude-haiku-4-5 (anthropic), gpt-4o-mini (openai), gemini-2.0-flash (gemini), llama3.2 (ollama) and \"default\" (vllm). The local provider ignores it and uses the tagger.local settings. A change affects memories tagged from then on.",
        "example": "\"qwen2.5:7b\" with tagger.llm_provider \"ollama\", after `ollama pull qwen2.5:7b`.",
    },
    "tagger.llm_base_url": {
        "help": "The API address for the OpenAI-compatible providers (openai, ollama, vllm and gemini). Empty uses the provider's standard address: http://localhost:11434/v1 for ollama, http://localhost:8000/v1 for vllm, and the official endpoints for openai and gemini; anthropic ignores it. Set it when your server runs on another port or host. A non-loopback address sends tagged text (secrets redacted) to that host, and recall then does not use it as a local model.",
        "example": "\"http://192.168.1.20:8000/v1\": to tag with a vLLM server on another machine in your network.",
    },
    "tagger.local.model_repo": {
        "help": "The Hugging Face repository the local helper model (a GGUF file) downloads from on first use; the default is the bundled helper, unsloth/Qwen3.5-4B-GGUF. The helper serves LLM tagging, local recall, summaries and model-based project analysis, so a change affects all of them. Set tagger.local.model_file to a file that exists in the new repository.",
        "example": "\"unsloth/Qwen3-8B-GGUF\" with a matching model file: a larger helper on a machine with plenty of RAM or VRAM.",
    },
    "tagger.local.model_file": {
        "help": "The GGUF file to download from tagger.local.model_repo into Imprint's models folder; the default is Qwen3.5-4B-Q4_K_M.gguf. The file picks the quantization (how much the model is compressed): smaller files use less memory and run faster but answer somewhat worse. Only the default file is checked against a pinned checksum.",
        "example": "\"Qwen3.5-4B-Q8_0.gguf\": a less compressed variant from the same repository, if you have the memory for it.",
    },
    "tagger.local.model_path": {
        "help": "The full path to a GGUF file already on disk. When set, it replaces model_repo and model_file and nothing is downloaded; if the file is missing, loading fails with an error. Leave it empty to use the managed download.",
        "example": "\"/models/Qwen3.5-4B-Q4_K_M.gguf\": to reuse a model file you downloaded for another tool, or on an offline machine.",
    },
    "tagger.local.n_ctx": {
        "help": "The local helper's context window: how many tokens of prompt and reply it handles at once (8192 by default). A larger window fits bigger passages but uses more RAM or VRAM; a smaller one saves memory, but long inputs are then split or rejected. It applies wherever the helper runs: tagging, recall, summaries and project analysis.",
        "example": "4096: on a laptop with little RAM where the helper fails to load at 8192.",
    },
    "tagger.local.n_gpu_layers": {
        "help": "How many of the local helper model's layers run on the GPU. -1 (the default) puts them all there, the fastest choice when a supported GPU is present; 0 runs on the CPU only; a number in between splits the model when it does not fit in VRAM. Lower it when loading fails with out-of-memory errors or the GPU is needed elsewhere.",
        "example": "0: to keep the helper on the CPU on a machine whose GPU is busy with games or other models.",
    },
    # ── Redaction ────────────────────────────────────────────────────────
    "redact.privacy_filter": {
        "help": "Rules that remove known credential formats always run before anything is stored. This adds OpenAI's Privacy Filter model, which also catches secrets and account numbers the rules miss; names, emails and addresses are deliberately kept. It runs locally on the CPU, downloads about 810 MB on first use and adds seconds per chunk. If it cannot run, nothing is stored and ingestion reports an error. Changing it makes the next refresh or index re-process indexed sources; memories stored directly are not redacted again.",
        "example": "true: when indexing exported chats or notes that may contain pasted passwords or bank details in free text.",
    },
    # ── Ingestion ────────────────────────────────────────────────────────
    "ingest.max_file_bytes": {
        "help": "The largest plain-text or code file, in bytes, that indexing reads (about 2 MB by default); documents such as PDFs do not use this limit. A larger file is reported as an error rather than cut short, and chunks it already had stay. Raise it to index large logs, dumps or generated files, at the cost of more chunks and slower indexing.",
        "example": "4000000: to index a 3 MB generated API reference that keeps failing the default limit.",
    },
    "ingest.doc_formats": {
        "help": "The document types the file scanner picks up: pdf, docx, pptx, xlsx, csv (also .tsv), epub, rtf, html (.html/.htm), eml (.eml/.mbox) and json (.json/.jsonc); all of them by default. Removing a type makes new scans skip those files but does not delete documents already indexed; an empty value turns document ingestion off. Images and audio follow ingest.ocr_enabled and audio.enabled.",
        "example": "\"pdf,docx,html\": to keep spreadsheets, mailboxes and JSON data files from flooding a project's memory.",
    },
    "ingest.ocr_enabled": {
        "help": "Local OCR (reading text out of images) for image files and for PDFs without a text layer, such as scans. Everything runs on this machine with models installed alongside Imprint. Off, image files are not picked up and scanned PDFs yield no text. Documents that yielded no text before are read again on the next index after a change.",
        "example": "false: on a slow machine where photos and screenshots in your folders make indexing crawl and you don't need their text.",
    },
    "ingest.ocr_lang": {
        "help": "The language OCR reads. \"eng\" (the default) and \"auto\" use the bundled OCR models, which read English and Chinese and need nothing else. Any other code goes to Tesseract, which must be installed with its language data and the pytesseract package, or OCR fails for those files. A change applies to documents read from then on; scans that yielded no text before are retried on the next index.",
        "example": "\"deu\": to OCR German scans, with Tesseract and its German language data installed.",
    },
    "ingest.max_doc_size_mb": {
        "help": "Meant as a size limit for document extraction (25 MB by default), but no ingestion code reads it, so it currently has no effect. Plain-text files are limited by ingest.max_file_bytes and audio by the audio limits.",
        "example": "25, the default: changing it currently does nothing.",
    },
    "ingest.url_timeout_sec": {
        "help": "How many seconds to wait while connecting to a site when ingesting a URL (30 by default). The same limit bounds the quick change check on refresh and each page load with the obscura browser. Raise it for slow or distant servers that time out; lower it to give up on dead links sooner.",
        "example": "60: when ingesting docs from a slow intranet server that often times out on connect.",
    },
    "ingest.url_read_timeout_sec": {
        "help": "How many seconds a URL download may wait for its next piece of data before giving up (300 by default). It limits stalls, not the total download time. Raise it for very large files on slow or bursty connections; lower it to give up sooner on servers that hang.",
        "example": "900: when ingesting a several-hundred-MB PDF from a server that pauses mid-download.",
    },
    "ingest.url_user_agent": {
        "help": "The User-Agent header Imprint sends with every URL request, including link discovery and change checks (\"imprint/1.0\" by default). Change it when a site blocks or serves different content to unknown clients, or asks crawlers to identify themselves with contact details.",
        "example": "\"imprint/1.0 (+mailto:you@example.com)\": so a docs site's admins can contact you about your crawling.",
    },
    "ingest.url_respect_robots": {
        "help": "Meant to make URL ingestion check a site's robots.txt (its rules for automated crawlers) before fetching, but the fetcher does not read robots.txt yet, so this has no effect either way. Keep crawls small with ingest.url_follow_max, and only ingest sites you are allowed to.",
        "example": "true, the default: it currently does not change fetching.",
    },
    "ingest.use_kreuzberg": {
        "help": "Extracts documents with the kreuzberg library instead of the built-in PDF, DOCX and image extractors, and adds archives (.zip, .tar, .7z) and other document types. Install it yourself (`pip install kreuzberg`, or `kreuzberg[all]`); it is licensed under the Elastic License 2.0, which forbids offering it as a hosted service. A change applies to documents read from then on; those that yielded no text before are retried on the next index.",
        "example": "true, after `pip install kreuzberg[all]`: to index zipped document bundles and get better table extraction.",
    },
    "ingest.use_obscura": {
        "help": "Fetches HTML pages with the obscura headless browser, which runs a page's JavaScript before extracting its text and links. That helps with single-page apps and JavaScript-heavy docs sites that otherwise come back nearly empty, but it is slower. The obscura binary must be on your PATH; if it is missing or fails, Imprint falls back to a normal fetch.",
        "example": "true: to ingest a React-rendered documentation site whose pages come back empty with a plain fetch.",
    },
    "ingest.url_follow": {
        "help": "When on, ingesting a URL also follows links to other pages on the same domain, as far as ingest.url_follow_depth and as many as ingest.url_follow_max allow. Off by default, so only the URLs you give are fetched. The --follow and --no-follow options override it for one run. On means more requests and more memories per ingest.",
        "example": "true: to pull in a whole small docs section by ingesting only its index page.",
    },
    "ingest.url_follow_depth": {
        "help": "How many link hops to follow from the URLs you gave, when link following is on. 1 (the default) fetches the pages those pages link to directly; 2 also fetches the pages they link to, and so on. The page count grows fast with each hop and is capped by ingest.url_follow_max. --follow-depth overrides it for one run.",
        "example": "2: to reach individual articles from a docs index that only links to section pages.",
    },
    "ingest.url_follow_max": {
        "help": "The most linked pages one ingest run adds to its queue while following links (20 by default); the URLs you give do not count. It is the main safeguard against an accidental site-wide crawl. Raise it for larger doc sets: each extra page costs a fetch and adds memories.",
        "example": "200: to ingest a complete mid-sized library reference with url_follow on and depth 2.",
    },
    # ── Project associations ─────────────────────────────────────────────
    "associations.enabled": {
        "help": "After a source is ingested or imported, Imprint looks in its text for the names and folder paths of your other registered projects and suggests linking the source to them; accepted links make its memories show up in searches for those projects too. The scan only matches names and paths, without a model. Off, no new suggestions appear, but accepted links keep working.",
        "example": "false: if you keep many projects with generic names and the suggestions fill with noise.",
    },
    "associations.auto_accept_paths": {
        "help": "When on, a suggested link is accepted automatically if the source contains the exact registered folder path of another project, and takes effect in search at once. Matches on a project's name alone, or found by the optional local model, always wait for your review; links you already accepted or rejected stay as they are. Off by default, so every link waits for review.",
        "example": "true: when your notes routinely cite full repository paths like /home/you/code/billing-api and you trust those as links.",
    },
    "associations.unassigned_workspace": {
        "help": "Imprint uses the working directory recorded in each AI conversation to choose the workspace that learns it, and skips conversations without one (for example from a desktop chat app). A workspace chosen here receives those conversations, like an inbox. It never overrides a recorded directory that belongs to another workspace, and it is cleared if that workspace is deleted.",
        "example": "\"personal\": so desktop-app chats not tied to a project folder are still remembered, in your personal workspace.",
    },
    # ── Audio ingestion ──────────────────────────────────────────────────
    "audio.enabled": {
        "help": "Transcribes audio files (.wav, .mp3, .m4a, .flac, .ogg, .opus and others) during ingestion into timestamped text, with a speech model that runs locally on the CPU; the model chosen in audio.model downloads on first use, and audio never leaves the machine. Off, the file scanner ignores audio. Transcription is CPU-heavy and takes a while for long recordings.",
        "example": "false: if your project folders hold music or sound assets you don't want transcribed.",
    },
    "audio.model": {
        "help": "The speech-to-text model. Both handle many languages and run locally on the CPU: \"large-v3-turbo\" (the default, about a 1.5 GiB download) is more accurate; \"small\" (about 464 MiB) is faster and lighter but makes more mistakes. A change re-transcribes audio files the next time they are indexed.",
        "example": "\"small\": on an older laptop where transcribing an hour-long meeting with the default model takes too long.",
    },
    "audio.language": {
        "help": "The spoken language. \"auto\" (the default) detects it for each stretch of about 30 seconds and keeps the text in that language, so mixed-language recordings work; a language code forces it and skips detection, which helps when detection guesses wrong on short or accented clips. A change re-transcribes audio the next time it is indexed.",
        "example": "\"ro\": for Romanian meeting recordings that detection sometimes mistakes for another language.",
    },
    "audio.max_file_size_mb": {
        "help": "The largest audio file, in MiB, that is transcribed (1024, 1 GiB, by default); bigger files fail with an error. It keeps huge files from tying up the CPU. A change also re-transcribes existing audio the next time it is indexed.",
        "example": "2048: to transcribe long uncompressed WAV recordings of workshops.",
    },
    "audio.max_duration_minutes": {
        "help": "The longest recording, in minutes, that is transcribed (120 by default), checked against the file's stated length and again while decoding; longer audio fails with an error. CPU transcription time grows with length. A change also re-transcribes existing audio the next time it is indexed.",
        "example": "240: to transcribe a four-hour conference session.",
    },
    # ── Voice ────────────────────────────────────────────────────────────
    "voice.runtime_dir": {
        "help": "The folder for Echo's live voice runtime, which voice setup unpacks or builds. Empty uses the voice folder in Imprint's data directory. Setup needs a few gigabytes free there: about 2 GB with the prebuilt bundle, 4 GB when building. Set it to put the runtime on a larger disk or reuse one you already built.",
        "example": "\"/mnt/data/imprint-voice\": when your home drive is short on space.",
    },
    "voice.avatar": {
        "help": "How Echo, the voice companion, looks and sounds: \"echo\" (the default) is a synaptic core with a robotic voice, \"iris\" has a female voice and \"kai\" a male one. It changes appearance and voice only, not the answers, from the next voice session.",
        "example": "\"iris\": if you prefer a natural female voice to the robotic one.",
    },
    # ── Chat ─────────────────────────────────────────────────────────────
    "chat.enabled": {
        "help": "Turns chat off for the standalone graph viewer, which then hides its chat button. The dashboard's Chat page does not read this setting, so it stays available there.",
        "example": "false: to hide the chat button in the graph viewer on a machine where you never use chat.",
    },
    "chat.provider": {
        "help": "Which model answers in chat. \"local\" (the default) runs a bundled llama.cpp model on this computer, and nothing leaves the machine. \"ollama\" and \"vllm\" use a model server you run, on localhost by default. \"anthropic\", \"openai\" and \"gemini\" send your messages, the system prompt (which includes this device's name and working directory) and the memory excerpts chat looks up to that company, and need ANTHROPIC_API_KEY, OPENAI_API_KEY or GOOGLE_API_KEY in the environment. Voice conversations with the chat engine need \"local\".",
        "example": "\"ollama\" with chat.model \"qwen3:8b\": to use a model you already run in Ollama.",
    },
    "chat.model": {
        "help": "The model name sent to a server provider. Empty uses each provider's default: claude-haiku-4-5 (anthropic), gpt-4o-mini (openai), gemini-2.0-flash (gemini), llama3.2 (ollama) or \"default\" (vllm). The local provider ignores it and uses chat.model_repo, chat.model_file or chat.model_path.",
        "example": "\"gpt-4.1-mini\" with chat.provider \"openai\": a newer OpenAI model than the default.",
    },
    "chat.base_url": {
        "help": "The address chat uses to reach a server provider that speaks the OpenAI-style API. Empty uses the provider's standard address: http://localhost:11434/v1 for ollama, http://localhost:8000/v1 for vllm, the vendor's own API for anthropic, openai and gemini. Set it when Ollama or vLLM runs on another port or machine, or to use an OpenAI-compatible gateway; messages and memory excerpts then go to that machine. For ollama and vllm, IMPRINT_CHAT_API_KEY supplies a key. The local provider ignores it.",
        "example": "\"http://192.168.1.20:11434/v1\" with chat.provider \"ollama\": when Ollama runs on another computer on your network.",
    },
    "chat.model_repo": {
        "help": "The Hugging Face repository the local chat model downloads from the first time you chat; the default is Imprint's tested Qwen3.5 4B model, checked against a known checksum. Change it, together with chat.model_file, to try another GGUF model (a single-file model format for llama.cpp); gated repositories need huggingface.token. Local provider only; chat.model_path replaces it.",
        "example": "\"unsloth/Qwen3-8B-GGUF\" with chat.model_file \"Qwen3-8B-Q4_K_M.gguf\": a larger local model on a machine with a big GPU.",
    },
    "chat.model_file": {
        "help": "The GGUF file to fetch from chat.model_repo into Imprint's models folder. A repository often offers several sizes of one model: smaller quantizations (lower-precision copies, such as Q4) load faster and use less memory, at some cost in quality. Local provider only; chat.model_path replaces it.",
        "example": "\"Qwen3.5-4B-Q8_0.gguf\": a higher-precision copy of the default model, if you have memory to spare.",
    },
    "chat.model_path": {
        "help": "The full path to a GGUF model already on disk. When set, it replaces chat.model_repo and chat.model_file and nothing is downloaded; if the file is missing, chat reports an error. Leave it empty to let Imprint download and manage the model. Local provider only.",
        "example": "\"/home/me/models/Mistral-Nemo-Instruct-Q4_K_M.gguf\": to reuse a model you downloaded for another tool.",
    },
    "chat.n_ctx": {
        "help": "Chat's context window: how many tokens (word pieces of about 4 characters) the model considers at once, conversation and memory lookups together (8192 by default). With the local provider, more lets chat read more before it condenses, but uses more GPU or system memory and loads more slowly. With a remote provider, Imprint only uses it to decide when to condense long evidence. Raise it if you see \"Chat context exceeded\" and have memory to spare.",
        "example": "16384: on a GPU with enough memory, so long source files fit without section analysis.",
    },
    "chat.n_gpu_layers": {
        "help": "How many layers of the local chat model run on the GPU. -1 (the default) puts them all there, the fastest choice; 0 runs on the CPU only, slower but using no GPU memory; a number in between splits the model when it does not fit in GPU memory. Local provider only.",
        "example": "0: on a laptop whose GPU is needed for other work, or runs out of memory.",
    },
    "chat.max_tokens": {
        "help": "The longest reply, in tokens, the model may write in one turn. That space is held back in the context window, so a higher value leaves less room for memory evidence. Raise it if answers are cut off; lower it for shorter, faster replies, or to spend less with a paid provider.",
        "example": "2048: when you ask for long explanations or code and replies stop mid-sentence.",
    },
    "chat.temperature": {
        "help": "How random the chat model's wording is. Low values, like the default 0.3, keep answers focused and repeatable; higher ones, up to about 1, make them more varied but likelier to drift from the evidence. The section workers that read long text always use 0.1.",
        "example": "0.1: for the most consistent, literal answers about your code and decisions.",
    },
    "chat.research_timeout_sec": {
        "help": "How many seconds chat may spend looking things up before it answers with what it has (300 by default, 10 to 3600); loading the model and writing the final answer are not counted. Raise it for deep questions on a slow machine; lower it for quicker, possibly less thorough answers.",
        "example": "120: for quicker answers on a fast GPU, where five minutes of research is more than you need.",
    },
    "chat.research_token_budget": {
        "help": "A cap on the model tokens one chat answer may use while researching: each request's prompt plus its maximum reply, section workers included; prompts to remote providers are estimated (65536 by default, 1024 to 2,000,000). At the cap, chat answers with the evidence it has. With a paid provider, this roughly limits what one question can cost.",
        "example": "32768 with chat.provider \"anthropic\": to keep the API cost of each question down.",
    },
    "chat.research_no_progress_rounds": {
        "help": "How many lookup rounds in a row may find nothing new before chat stops researching and answers (4 by default, 2 to 20); repeated results, loops and recurring errors count as no progress. Lower values answer sooner; higher ones let the model keep digging when early searches miss.",
        "example": "6: if chat often gives up before finding something you know is in memory.",
    },
    "chat.analysis_workers": {
        "help": "When a question or a looked-up document is too big for the context window, chat splits it into sections that these workers read one after another, taking notes. Each worker keeps its own notes, so more workers keep more separate detail, but each gets less room for notes. 0 turns this off, and oversized input then fails with \"Chat context exceeded\". With a remote provider, every section is sent to it. 2 by default, up to 8.",
        "example": "4: when you often ask about very long files or transcripts and want more detail kept.",
    },
    "chat.tool_format": {
        "help": "How the local chat model writes tool calls, which is how it searches your memory. \"auto\" (the default) reads it from the model's chat template; if it finds no known format, chat runs without memory tools and says so. Set it only for a custom model that can call tools but is not detected (qwen-xml, hermes, llama3 or mistral); \"none\" chats without tools. Local provider only.",
        "example": "\"hermes\": for a Qwen2.5 or Hermes GGUF in chat.model_path whose tool calls are not detected.",
    },
    # ── Session summaries ────────────────────────────────────────────────
    "summarizer.enabled": {
        "help": "After an agent session ends (on its Stop hook), a language model reads the transcript and saves short memories of its decisions, bug causes, reusable patterns, durable facts and a 2–4 sentence recap. Off by default; on, it adds model work after every session and, with a remote provider, API cost.",
        "example": "true with summarizer.provider \"local\": session recaps without anything leaving your machine.",
    },
    "summarizer.provider": {
        "help": "Which model writes session summaries. \"local\" (the default) reuses the tagger's local model (the tagger.local settings), so nothing leaves the machine. \"ollama\" and \"vllm\" use your own model server, on localhost by default. \"anthropic\", \"openai\" and \"gemini\" send the transcript, with the secrets Imprint recognizes redacted, to that company, spend API tokens, and need ANTHROPIC_API_KEY, OPENAI_API_KEY or GOOGLE_API_KEY; without the key, the summary is skipped.",
        "example": "\"ollama\" with summarizer.model \"qwen3.5:4b\": summaries from a model Ollama serves on this computer.",
    },
    "summarizer.model": {
        "help": "The model name requested from ollama, vllm, anthropic, openai or gemini. The default, qwen3.5:4b, is an Ollama-style name, so change it for another provider. The local provider ignores it and uses the tagger's local model.",
        "example": "\"claude-haiku-4-5\" with summarizer.provider \"anthropic\": summaries from Anthropic's small model.",
    },
    "summarizer.base_url": {
        "help": "The server address the summarizer sends transcripts to. Empty uses http://localhost:11434/v1 for ollama, http://localhost:8000/v1 for vllm, and the vendor's standard API for openai and gemini; local and anthropic ignore it. Set it when your model server runs on another port or machine.",
        "example": "\"http://localhost:8001/v1\" with summarizer.provider \"vllm\": when vLLM listens on a non-default port.",
    },
    "summarizer.min_messages": {
        "help": "Sessions with fewer user messages than this are not summarized, so short exchanges create no noise or cost (5 by default). Raise it to summarize only substantial sessions; lower it if short but important ones are skipped.",
        "example": "10: to summarize only longer working sessions.",
    },
    "summarizer.max_input_tokens": {
        "help": "How much of a transcript, in estimated tokens of about 4 characters, the summarizer reads (5000 by default). Longer transcripts lose their oldest messages and keep the newest; the local provider also trims to what fits its model. More covers more of long sessions but is slower, and costs more with paid providers.",
        "example": "20000 with summarizer.provider \"openai\": to summarize whole long sessions with a large-context remote model.",
    },
    # ── Recall ───────────────────────────────────────────────────────────
    "recall.llm": {
        "help": "Lets the recall tool, which agents use to gather several memory lookups in one call, ask a model on this computer to plan extra lookups and write a short answer with numbered citations. It only works when tagger.llm_provider is \"local\", or ollama/vllm at a localhost address, and never uses a remote provider, so questions and evidence stay on the machine. Recall does not download the model; if it is missing, slow or fails, recall returns the plain results with a note. Off by default.",
        "example": "true: when the tagger already runs a local model and you want recall to return cited answers, not only raw results.",
    },
    "recall.llm_timeout_sec": {
        "help": "How many seconds one recall waits for the local model, planning and answering together, before it returns the evidence without the model (30 by default). Only matters with recall.llm on. Raise it on a slow CPU-only machine; lower it if agents wait too long.",
        "example": "60: on a CPU-only machine where the local model needs more time to plan.",
    },
    # ── Workspaces and updates ───────────────────────────────────────────
    "workspace.unassigned": {
        "help": "The workspace (a separate set of memories) used by agent sessions that no project assigns one, such as desktop app chats, or folders without an approved .imprint/workspace.json or workspace root. Empty (the default) uses whichever workspace is active in the CLI and dashboard at the time; set it to keep such sessions in one fixed workspace. It resets to empty if that workspace is removed.",
        "example": "\"personal\": so Claude Desktop chats always use your personal workspace, not whichever work workspace is active.",
    },
    "update.channel": {
        "help": "Which releases `imprint update` and the dashboard's update check offer. \"stable\" (the default) offers releases from main only; \"dev\" also offers prerelease builds from the dev branch, which are less tested, and stable releases once they are newer. --stable or --dev overrides it for one `imprint update`.",
        "example": "\"dev\": to try fixes before they reach a stable release, on a machine where occasional breakage is acceptable.",
    },
    "collection": {
        "help": "The name of the Qdrant collection (the table of vectors) the \"default\" workspace stores its memories in; other workspaces always use \"memories_<workspace>\". Changing it points the default workspace at a different collection, empty or not: the old memories stay where they were but are no longer searched. Use workspaces to keep memories apart, and leave this alone.",
        "example": "\"memories\", the default: set it back if search suddenly returns nothing after it was changed.",
    },
}
