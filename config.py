"""Day-1 Atlas test settings. All endpoints/fields follow the Atlas docs."""
BASE_URL   = "https://atlas.oneinbox.ai"
LISTEN_MODEL = "atlas-listen"
SPEAK_MODEL  = "atlas-speak"

# --- Speak (TTS) test inputs (defaults; edit freely) ---
SPEAK_VOICE = "souta"                      # default voice per docs
# The 12 Japanese voices atlas-speak offers (Speak > Voices). Set SPEAK_VOICE above
# to any of these. English text is spoken with a Japanese voice (per docs).
SPEAK_VOICES = ["ayaka", "emi", "kaito", "sakura", "souta", "nanami",
                "haruto", "mei", "yuto", "rina", "ren", "hajun"]
ENGLISH_TEXTS = [
    "Thank you for calling the bank. How can I help you with your account today?",
    "Your current balance is 2,450 dollars and your last payment of 60 dollars cleared today.",
]
# Latency pass target for time-to-first-audio (ms). Report-only if None.
FIRST_AUDIO_TARGET_MS = 1000

# --- Pronunciation dictionary test ---
# The dictionary lives on your KEY (docs). We cannot read it, only hear it.
# Put words your key has custom readings for; leave empty to skip the test.
PRON_WORDS = []            # e.g. ["OneInbox", "Exotel"]

# --- Concurrency test ---
CONCURRENCY_ATTEMPT = 6    # open this many at once; expect the limit+1 to be refused

# --- Day-2 speech-to-speech pipeline: the LLM in the middle ---
# Atlas has NO LLM (chat 404s), so the "reply" step uses a separate OpenAI-compatible
# provider. Default = Groq (free, fast). To switch providers, change these three lines.
LLM_BASE_URL = "https://api.groq.com/openai/v1"   # OpenAI-compatible base
LLM_MODEL    = "openai/gpt-oss-20b"               # a model this key can use
LLM_KEY_ENV  = "GROQ_API_KEY"                      # env var holding the LLM key
ASSISTANT_SYSTEM = (
    "You are a friendly, concise voice assistant for a retail bank and financial-services "
    "call center. Your ONLY job is to help the caller with banking and personal-finance "
    "requests: account balance, recent transactions, card issues (block or replace a card), "
    "payments and fund transfers, bill payments, loan and credit-card information, interest "
    "rates, and general account support. "
    "Use the conversation so far and remember details already given (name, account type, "
    "amount, payee, etc.) — never ask again for something already provided. Move the request "
    "forward: confirm what you have and ask only for the ONE missing piece. "
    "SECURITY: for anything sensitive, ask the caller to verify their identity politely, but "
    "NEVER ask them to say a full card number, CVV, PIN, OTP, or password aloud. "
    "STRICT RULE: if the caller asks about anything NOT related to banking or personal finance "
    "(general knowledge, math, jokes, poems, coding, weather, news, travel, etc.), do NOT answer "
    "it. Politely say you can only help with banking and steer back, e.g. 'I can only help with "
    "banking and account questions. How can I help with your account?' "
    "FORMAT RULES (important): reply with ONLY your single next line as the agent — ONE short "
    "sentence, at most about 20 words. Never write the caller's words, never produce multiple "
    "questions or multiple turns in one reply, and ask for only ONE piece of missing info at a "
    "time. No lists, markdown, or emojis."
)
