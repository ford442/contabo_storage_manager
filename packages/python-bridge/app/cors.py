"""Helpers for configuring CORS middleware consistently."""


def build_cors_middleware_options(cors_origins: str, cors_origin_regex: str) -> dict:
    """Build CORSMiddleware kwargs from environment-style settings."""
    allowed_origins = [origin.strip() for origin in cors_origins.split(",") if origin.strip()]
    origin_regex = cors_origin_regex.strip() or None

    options = {
        "allow_origins": allowed_origins,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "expose_headers": ["*"],
        "max_age": 86400,
    }

    if "*" in allowed_origins:
        options["allow_credentials"] = False
    else:
        options["allow_credentials"] = True
        options["allow_origin_regex"] = origin_regex

    return options
