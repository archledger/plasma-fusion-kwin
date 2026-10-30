# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded complete-page retrieval for GitHub evidence lists."""
from ci.native.qualification import QualificationError, identifier, require


def get_all(api, resource, *, key=None, per_page=100, max_pages=5, check_filter=None):
    """Require a short terminal page; a full last allowed page is not completeness.

    List responses retain duplicates for caller-specific consistency checks.
    Object responses with a total_count must have consistent counts and unique
    record IDs. This is not an atomic snapshot of a changing remote list.
    """
    try:
        require(type(per_page) is int and 1 <= per_page <= 100, 'invalid evidence page size')
        require(type(max_pages) is int and 1 <= max_pages <= 5, 'invalid evidence page budget')
        require(key is None or (type(key) is str and 0 < len(key) <= 64), 'invalid list key')
        result, expected_total = [], None
        for page in range(1, max_pages + 1):
            options = {'page': page, 'per_page': per_page}
            if check_filter is not None:
                options['check_filter'] = check_filter
            value = api.get(resource, **options)
            if key is not None:
                require(type(value) is dict and type(value['total_count']) is int
                        and 0 <= value['total_count'] <= per_page * max_pages,
                        'invalid evidence total count')
                if expected_total is None:
                    expected_total = value['total_count']
                require(value['total_count'] == expected_total, 'evidence count changed across pages')
                value = value[key]
            require(type(value) is list and len(value) <= per_page
                    and all(type(item) is dict for item in value), 'invalid evidence page')
            result.extend(value)
            if len(value) < per_page:
                if expected_total is not None:
                    require(len(result) == expected_total
                            and len({identifier(item['id']) for item in result}) == len(result),
                            'missing or duplicate evidence records')
                return result
        raise QualificationError('evidence pagination limit reached without a terminal page')
    except QualificationError:
        raise
    except (ValueError, KeyError, TypeError, IndexError):
        raise QualificationError('invalid or unavailable paginated evidence') from None
