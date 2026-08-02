# stapel-notifications

[![CI](https://img.shields.io/github/actions/workflow/status/usestapel/stapel-notifications/ci.yml?branch=main&logo=github&label=CI)](https://github.com/usestapel/stapel-notifications/actions/workflows/ci.yml?query=branch%3Amain)
[![coverage](https://img.shields.io/codecov/c/github/usestapel/stapel-notifications?branch=main&logo=codecov&label=coverage)](https://app.codecov.io/gh/usestapel/stapel-notifications)
[![pypi](https://img.shields.io/pypi/v/stapel-notifications?logo=pypi&logoColor=white&label=pypi)](https://pypi.org/project/stapel-notifications/)
[![downloads](https://static.pepy.tech/badge/stapel-notifications/month)](https://pepy.tech/project/stapel-notifications)
[![python](https://img.shields.io/pypi/pyversions/stapel-notifications?logo=python&logoColor=white)](https://pypi.org/project/stapel-notifications/)
[![license](https://img.shields.io/github/license/usestapel/stapel-notifications)](https://github.com/usestapel/stapel-notifications/blob/main/LICENSE)

> Notifications — push (Firebase), email, SMS channels with delivery logging

Part of the [Stapel framework](https://github.com/usestapel) — composable Django apps for building production-grade platforms.

**Error reference:** [Errors (EN)](docs/errors.en.md) · [Ошибки (RU)](docs/errors.ru.md)

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
