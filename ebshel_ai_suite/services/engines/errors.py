# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Engine error hierarchy.

Every failure coming from an engine is converted into one of these classes.
``public_message`` is safe to display to any user; ``detail`` may contain
technical information and is only shown to AI administrators (it is always
passed through :func:`redact_secrets` before being stored).
"""
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)


class EngineError(Exception):
    """Base class for all engine failures."""
    code = 'engine_error'
    public_text = _lt('The AI service could not complete the request. Please try again later.')
    retryable = False

    def __init__(self, detail: str = '', public_text=None):
        super().__init__(detail or self.code)
        self.detail = detail
        if public_text is not None:
            self.public_text = public_text

    def public_message_for(self, env) -> str:
        """User-safe message translated in the language of ``env``."""
        return env._(self.public_text)

    @property
    def public_message(self) -> str:
        """Untranslated (English) message, for logs and technical contexts."""
        return self.public_text._translate('en_US') if hasattr(self.public_text, '_translate') \
            else str(self.public_text)


class EngineAuthError(EngineError):
    code = 'auth'
    public_text = _lt('The AI service could not authenticate the configured provider. '
                      'Please contact an administrator.')


class EngineTimeout(EngineError):
    code = 'timeout'
    public_text = _lt('The AI service took too long to answer. Please try again.')
    retryable = True


class EngineRateLimited(EngineError):
    code = 'rate_limited'
    public_text = _lt('The AI provider is receiving too many requests. Please retry in a moment.')
    retryable = True


class EngineUnavailable(EngineError):
    code = 'unavailable'
    public_text = _lt('The AI provider is currently unreachable. Please try again later.')
    retryable = True


class EngineBadResponse(EngineError):
    code = 'bad_response'
    public_text = _lt('The AI provider returned an unexpected answer. Please try again.')


class EngineRequestRejected(EngineError):
    code = 'rejected'
    public_text = _lt('The AI provider rejected the request. Please rephrase it or contact an administrator.')


class EngineConfigError(EngineError):
    code = 'config'
    public_text = _lt('The AI connection is not configured correctly. Please contact an administrator.')


class UsageLimitReached(EngineError):
    code = 'limit'
    public_text = _lt('The AI usage limit has been reached. Please try again later or contact an administrator.')
