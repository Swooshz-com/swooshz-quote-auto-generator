"""Strict wire contract for the server's image observation stage."""

from webapp.ai_response_contracts import ContractViolation, Field, ResponseContract, obj


# Match the server's per-batch limits. The server owns total coverage and ordering.
MAX_IMAGES = 6
MAX_RESULT_CHARS = 16_000
IMAGE_OBSERVATIONS = ResponseContract("draft_image_observations_v1", obj(
    observations=Field("array", item=Field("string", nonblank=True)),
))


def is_image_batch_input(messages):
    if len(messages) != 1:
        return False
    message = messages[0]
    if not isinstance(message, dict) or set(message) != {"role", "content"} or message["role"] != "user":
        return False
    parts = message["content"]
    if not isinstance(parts, list) or not 2 <= len(parts) <= 1 + MAX_IMAGES:
        return False
    prompt, *images = parts
    if (not isinstance(prompt, dict) or set(prompt) != {"type", "text"}
            or prompt["type"] != "input_text" or not isinstance(prompt["text"], str) or not prompt["text"].strip()):
        return False
    return all(
        isinstance(image, dict) and set(image) == {"type", "image_url", "detail"}
        and image["type"] == "input_image" and image["detail"] == "high"
        and isinstance(image["image_url"], str) and image["image_url"].startswith("data:image/")
        for image in images
    )


def admit_image_observations(text):
    """Validate the existing text before the gateway reports success."""
    if len(text) > MAX_RESULT_CHARS:
        raise ContractViolation("response_too_large")
    IMAGE_OBSERVATIONS.parse(text)
