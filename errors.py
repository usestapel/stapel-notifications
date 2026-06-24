"""Error constants for notifications service."""
from stapel_core.django.errors import register_service_errors

ERR_400_INVALID_PLATFORM = 'error.400.invalid_platform'
ERR_404_TOKEN_NOT_FOUND = 'error.404.token_not_found'

SERVICE_ERRORS = {
    ERR_400_INVALID_PLATFORM: 'Platform must be one of: ios, android, web.',
    ERR_404_TOKEN_NOT_FOUND: 'Device token not found.',
}

register_service_errors(SERVICE_ERRORS)
