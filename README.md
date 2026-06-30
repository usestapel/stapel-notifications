# stapel-notifications

[![CI](https://github.com/usestapel/stapel-notifications/actions/workflows/ci.yml/badge.svg)](https://github.com/usestapel/stapel-notifications/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/usestapel/stapel-notifications/graph/badge.svg)](https://codecov.io/gh/usestapel/stapel-notifications)

> Notifications — push (Firebase), email, SMS channels with delivery logging

Part of the [Stapel framework](https://github.com/usestapel) — composable Django apps for building production-grade platforms.

## Installation

```bash
pip install stapel-notifications
```

## Quick start

```python
# settings.py
INSTALLED_APPS = [
    ...
    'stapel_notifications',
]
```

## Bus events

### Consumes
| `user.deleted` | [schema](schemas/consumes/user.deleted.json) |
| `user.deletion_initiated` | [schema](schemas/consumes/user.deletion_initiated.json) |

## License

MIT — see [LICENSE](LICENSE)
