from django.core.cache import cache


def allow_once(key: str, seconds: int) -> bool:
    """True the first time `key` is seen in `seconds`; False while the cool-down is active.

    Used to stop unauthenticated endpoints (resend e-mail, forgot password) being used to
    flood someone's inbox. Uses Django's cache (per process with the default local-memory
    cache; configure a shared cache in production for a global limit).
    """
    return cache.add(f'cooldown:{key}', 1, seconds)
