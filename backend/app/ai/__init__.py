"""ARGUS AI layer (PRD 10 - bounded investigator providers).

Provider chain with automatic fallback:
    Google Gemini  ->  OpenAI  ->  Sarvam-M  ->  Ollama (local Llama)  ->  fake

Every backend speaks over a thin, injectable HTTP transport so tests never
touch the network. The chain is resolved from settings (ARGUS_AI_PROVIDER)
and the investigator runs its agentic tool loop on top of whichever backend
answers first.
"""
