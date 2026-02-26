from core.memory_sqlite import add_event
from core.rules import apply


def ingest(text, source="user", metadata=None):

    result = apply(text, source=source, metadata=metadata)

    add_event(source, text, tags=result.tags)

    return {
        "text": result.text,
        "intent": result.intent,
        "action": result.action,
        "tags": result.tags
    }


if __name__ == "__main__":
    import sys

    txt = sys.argv[1] if len(sys.argv) > 1 else "Test"
    res = ingest(txt)

    print("Vectrax:", res["text"])

