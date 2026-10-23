"""Description-only stack policy. Keep in sync with the backend job_feed policy."""

import re
import unicodedata

POLICY_VERSION = "1"
PATTERNS = (
    ("React", r"\breact(?:[.\s-]?js)?\b"),
    ("Next.js", r"\bnext[.\s-]?js\b"),
    ("Angular", r"\bangular\b"),
    ("Vue", r"\bvue(?:[.\s-]?js)?\b"),
    ("SvelteKit", r"\bsvelte[.\s-]?kit\b"),
    ("Express", r"\bexpress(?:[.\s-]?js)?\b"),
    ("NestJS", r"\bnest[.\s-]?js\b"),
    ("FastAPI", r"\bfast[.\s-]?api\b"),
)
COMPILED = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in PATTERNS]
# Avoid ordinary phrases or company names qualifying a job as an Express role.
NON_FRAMEWORK_EXPRESS = re.compile(
    r"\bamerican\s+express\b|\bexpress\s+(?:(?:your|their|our|my|the|an?)\s+)?"
    r"(?:interest|ideas?|opinions?|thoughts?|feelings?|concerns?|gratitude)\b",
    re.IGNORECASE,
)


def matching_technologies(description: str) -> list[str]:
    """One named framework is enough; generic Python/TypeScript do not qualify."""
    text = unicodedata.normalize("NFKC", description)
    express_text = NON_FRAMEWORK_EXPRESS.sub("", text)
    return [name for name, pattern in COMPILED
            if pattern.search(express_text if name == "Express" else text)]
