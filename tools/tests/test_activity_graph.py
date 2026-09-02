from datetime import date
from json import dumps, loads
from unittest import TestCase
from unittest.mock import Mock, call, patch

from tools.activity_graph import (
    ActivityChart,
    ContributionCalendar,
    ContributionHistory,
    Point,
    main,
)

SRC = 'tools.activity_graph'
def _connection(has_next=False, cursor='C1', nodes=()):
    return {
        'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor},
        'nodes': list(nodes),
    }


def _page(issues_next=False, issue_cursor='IS1'):
    return {
        'contributionCalendar': {'weeks': []},
        'commitContributionsByRepository': [],
        'pullRequestContributions': _connection(cursor='PR1'),
        'pullRequestReviewContributions': _connection(cursor='RV1'),
        'issueContributions': _connection(issues_next, issue_cursor),
    }


SERIES = {
    'Commits': {'2026-08-31': 4, '2026-09-01': 1, '2026-09-02': 0},
    'Pull requests': {'2026-08-31': 1, '2026-09-01': 1, '2026-09-02': 0},
    'Reviews': {'2026-08-31': 1, '2026-09-01': 0, '2026-09-02': 0},
    'Issues': {'2026-08-31': 0, '2026-09-01': 0, '2026-09-02': 0},
}


class MainTests(TestCase):
    """Unit tests for tools.activity_graph.main."""

    def setUp(t) -> None:  # pylint: disable=arguments-renamed
        for target in (
            'ActivityChart',
            'ContributionHistory',
            'Path',
            'datetime',
            'getenv',
        ):
            patcher = patch(f'{SRC}.{target}', autospec=True)
            setattr(t, target, patcher.start())
            t.addCleanup(patcher.stop)
        t.getenv.return_value = 'TOKEN'
        t.datetime.now.return_value.date.return_value = date(2026, 9, 2)

    def test_window(t) -> None:
        main(['--out', 'chart.svg'])

        t.ContributionHistory.assert_called_once_with(
            ('lundybernard', '3M1LY-lb'),
            date(2026, 8, 3),
            date(2026, 9, 2),
            'TOKEN',
        )
        t.getenv.assert_called_once_with('GH_TOKEN', '')

    def test_output(t) -> None:
        with t.subTest('given path'):
            main(['--out', 'chart.svg'])

            t.ActivityChart.assert_called_once_with(
                t.ContributionHistory.return_value.daily_totals,
                t.ContributionHistory.return_value.series,
            )
            t.Path.assert_called_once_with('chart.svg')
            t.Path.return_value.write_text.assert_called_once_with(
                t.ActivityChart.return_value.svg, encoding='utf-8'
            )

        with t.subTest('default path'):
            t.Path.reset_mock()

            main([])

            t.Path.assert_called_once_with('activity.svg')


class ContributionHistoryTests(TestCase):
    """Unit tests for tools.activity_graph.ContributionHistory."""

    def setUp(t) -> None:  # pylint: disable=arguments-renamed
        patcher = patch(f'{SRC}.ContributionCalendar', autospec=True)
        t.ContributionCalendar = patcher.start()
        t.addCleanup(patcher.stop)
        t.ch = ContributionHistory(
            ('alpha', 'beta'),
            date(2026, 8, 31),
            date(2026, 9, 2),
            'TOKEN',
        )

    def test_calendars(t) -> None:
        calendar = t.ContributionCalendar.return_value

        t.assertEqual(t.ch.calendars, (calendar, calendar))
        t.assertEqual(
            t.ContributionCalendar.call_args_list,
            [
                call('alpha', date(2026, 8, 31), date(2026, 9, 2), 'TOKEN'),
                call('beta', date(2026, 8, 31), date(2026, 9, 2), 'TOKEN'),
            ],
        )

    def test_daily_totals(t) -> None:
        first = Mock(spec=['daily_counts'])
        first.daily_counts = {'2026-08-30': 9, '2026-08-31': 4}
        second = Mock(spec=['daily_counts'])
        second.daily_counts = {'2026-08-31': 7, '2026-09-02': 1}
        t.ch.calendars = (first, second)

        t.assertEqual(
            t.ch.daily_totals,
            {'2026-08-31': 11, '2026-09-01': 0, '2026-09-02': 1},
        )

    def test_window(t) -> None:
        t.assertEqual(
            t.ch.window,
            ('2026-08-31', '2026-09-01', '2026-09-02'),
        )

    def test_series(t) -> None:
        first = Mock(spec=['series'])
        first.series = {
            'Commits': {'2026-08-30': 9, '2026-08-31': 2},
            'Issues': {'2026-09-02': 1},
        }
        second = Mock(spec=['series'])
        second.series = {'Commits': {'2026-08-31': 5}}
        t.ch.calendars = (first, second)

        t.assertEqual(
            t.ch.series,
            {
                'Commits': {
                    '2026-08-31': 7,
                    '2026-09-01': 0,
                    '2026-09-02': 0,
                },
                'Issues': {
                    '2026-08-31': 0,
                    '2026-09-01': 0,
                    '2026-09-02': 1,
                },
            },
        )


class ContributionCalendarTests(TestCase):
    """Unit tests for tools.activity_graph.ContributionCalendar."""

    def setUp(t) -> None:  # pylint: disable=arguments-renamed
        for target in ('Request', 'urlopen'):
            patcher = patch(f'{SRC}.{target}', autospec=True)
            setattr(t, target, patcher.start())
            t.addCleanup(patcher.stop)
        t.reply = t.urlopen.return_value.__enter__.return_value
        t.cc = ContributionCalendar(
            'alpha',
            date(2026, 8, 31),
            date(2026, 9, 2),
            'TOKEN',
        )

    def test_pages(t) -> None:
        with t.subTest('one request'):
            last = _page()
            t.reply.read.return_value = dumps(
                {'data': {'user': {'contributionsCollection': last}}}
            ).encode()

            t.assertEqual(t.cc.pages, (last,))

            t.assertEqual(
                t.Request.call_args.args,
                ('https://api.github.com/graphql',),
            )
            t.assertEqual(
                t.Request.call_args.kwargs['headers'],
                {
                    'Authorization': 'bearer TOKEN',
                    'Content-Type': 'application/json',
                },
            )
            body = loads(t.Request.call_args.kwargs['data'])
            t.assertEqual(
                body['variables'],
                {
                    'login': 'alpha',
                    'from': '2026-08-31T00:00:00Z',
                    'to': '2026-09-02T23:59:59Z',
                    'prCursor': None,
                    'reviewCursor': None,
                    'issueCursor': None,
                },
            )
            for field in (
                'contributionCalendar',
                'commitContributionsByRepository',
                'pullRequestContributions',
                'pullRequestReviewContributions',
                'issueContributions',
            ):
                t.assertIn(field, body['query'])

        with t.subTest('follows the cursor of an unread connection'):
            t.cc = ContributionCalendar(
                'alpha',
                date(2026, 8, 31),
                date(2026, 9, 2),
                'TOKEN',
            )
            more, last = _page(True, 'IS2'), _page()
            t.reply.read.side_effect = [
                dumps(
                    {'data': {'user': {'contributionsCollection': page}}}
                ).encode()
                for page in (more, last)
            ]

            t.assertEqual(t.cc.pages, (more, last))

            body = loads(t.Request.call_args.kwargs['data'])
            t.assertEqual(body['variables']['issueCursor'], 'IS2')

    def test_daily_counts(t) -> None:
        t.cc.pages = (
            {
                'contributionCalendar': {
                    'weeks': [
                        {
                            'contributionDays': [
                                {'date': '2026-08-31', 'contributionCount': 4},
                                {'date': '2026-09-01', 'contributionCount': 0},
                            ]
                        },
                        {
                            'contributionDays': [
                                {'date': '2026-09-02', 'contributionCount': 7},
                            ]
                        },
                    ]
                }
            },
        )

        t.assertEqual(
            t.cc.daily_counts,
            {'2026-08-31': 4, '2026-09-01': 0, '2026-09-02': 7},
        )

    def test_series(t) -> None:
        with t.subTest('counts every page, commits once'):
            t.cc.pages = (
                {
                    'commitContributionsByRepository': [
                        {
                            'contributions': {
                                'pageInfo': {'hasNextPage': False},
                                'nodes': [
                                    {
                                        'occurredAt': '2026-08-31T10:00:00Z',
                                        'commitCount': 3,
                                    },
                                    {
                                        'occurredAt': '2026-09-01T09:00:00Z',
                                        'commitCount': 2,
                                    },
                                ],
                            }
                        },
                        {
                            'contributions': {
                                'pageInfo': {'hasNextPage': False},
                                'nodes': [
                                    {
                                        'occurredAt': '2026-08-31T12:00:00Z',
                                        'commitCount': 4,
                                    },
                                ],
                            }
                        },
                    ],
                    'pullRequestContributions': {
                        'nodes': [{'occurredAt': '2026-08-31T08:00:00Z'}]
                    },
                    'pullRequestReviewContributions': {'nodes': []},
                    'issueContributions': {
                        'nodes': [{'occurredAt': '2026-09-02T08:00:00Z'}]
                    },
                },
                {
                    'commitContributionsByRepository': [
                        {
                            'contributions': {
                                'pageInfo': {'hasNextPage': False},
                                'nodes': [
                                    {
                                        'occurredAt': '2026-09-02T10:00:00Z',
                                        'commitCount': 99,
                                    },
                                ],
                            }
                        },
                    ],
                    'pullRequestContributions': {
                        'nodes': [{'occurredAt': '2026-08-31T09:00:00Z'}]
                    },
                    'pullRequestReviewContributions': {'nodes': []},
                    'issueContributions': {'nodes': []},
                },
            )

            t.assertEqual(
                t.cc.series,
                {
                    'Commits': {'2026-08-31': 7, '2026-09-01': 2},
                    'Pull requests': {'2026-08-31': 2},
                    'Reviews': {},
                    'Issues': {'2026-09-02': 1},
                },
            )

        with t.subTest('an unread commit page'):
            t.cc = ContributionCalendar(
                'alpha',
                date(2026, 8, 31),
                date(2026, 9, 2),
                'TOKEN',
            )
            t.cc.pages = (
                {
                    'commitContributionsByRepository': [
                        {
                            'contributions': {
                                'pageInfo': {'hasNextPage': True},
                                'nodes': [],
                            }
                        },
                    ],
                },
            )

            with t.assertRaisesRegex(
                RuntimeError, 'more commits than one page'
            ):
                t.cc.series


class ActivityChartTests(TestCase):
    """Unit tests for tools.activity_graph.ActivityChart."""

    def setUp(t) -> None:  # pylint: disable=arguments-renamed
        t.ac = ActivityChart(
            {'2026-09-02': 0, '2026-08-31': 6, '2026-09-01': 2},
            SERIES,
        )

    def test_ceiling(t) -> None:
        with t.subTest('rounds up to a multiple of the gridline count'):
            t.assertEqual(t.ac.ceiling, 8)

        with t.subTest('an exact multiple stands'):
            t.ac = ActivityChart({'2026-08-31': 44}, SERIES)

            t.assertEqual(t.ac.ceiling, 44)

        with t.subTest('no contributions'):
            t.ac = ActivityChart({'2026-08-31': 0}, SERIES)

            t.assertEqual(t.ac.ceiling, 4)

        with t.subTest('a type above the daily total'):
            t.ac = ActivityChart(
                {'2026-08-31': 2},
                {'Commits': {'2026-08-31': 9}},
            )

            t.assertEqual(t.ac.ceiling, 12)

    def test_points(t) -> None:
        t.assertEqual(
            t.ac.points,
            (
                Point(44.0, 102.5, '2026-08-31', 6),
                Point(462.0, 211.5, '2026-09-01', 2),
                Point(880.0, 266.0, '2026-09-02', 0),
            ),
        )

    def test_series_points(t) -> None:
        t.assertEqual(
            tuple(t.ac.series_points),
            ('Commits', 'Pull requests', 'Reviews', 'Issues'),
        )
        t.assertEqual(
            t.ac.series_points['Commits'],
            (
                Point(44.0, 157.0, '2026-08-31', 4),
                Point(462.0, 238.75, '2026-09-01', 1),
                Point(880.0, 266.0, '2026-09-02', 0),
            ),
        )

    def test_legend(t) -> None:
        t.assertEqual(
            t.ac.legend,
            (
                ('Total', 8),
                ('Commits', 5),
                ('Pull requests', 2),
                ('Reviews', 1),
                ('Issues', 0),
            ),
        )

    def test_svg(t) -> None:
        with t.subTest('document'):
            svg = t.ac.svg

            t.assertTrue(svg.startswith('<svg '), svg[:40])
            t.assertTrue(svg.endswith('</svg>\n'), svg[-40:])
            t.assertIn('viewBox="0 0 900 330"', svg)
            t.assertIn('>Recent Contributions<', svg)
            t.assertIn('#26a641', svg)
            t.assertIn('points="44.0,102.5 462.0,211.5 880.0,266.0"', svg)
            t.assertIn('<title>2026-08-31: 6</title>', svg)
            t.assertIn('<title>2026-09-02: 0</title>', svg)
            t.assertIn('>08-31<', svg)
            t.assertIn('>09-02<', svg)
            t.assertIn('y="288"', svg)

        with t.subTest('a group per series, the types under the total'):
            svg = t.ac.svg

            t.assertIn('<g aria-label="Total">', svg)
            for name in ('Commits', 'Pull requests', 'Reviews', 'Issues'):
                t.assertIn(f'<g aria-label="{name}">', svg)
            t.assertIn(
                '<desc>2026-08-31: 4\n2026-09-01: 1\n2026-09-02: 0</desc>',
                svg,
            )
            t.assertIn('#2a78d6', svg)
            t.assertIn('stroke-dasharray="5 3"', svg)
            t.assertLess(
                svg.index('aria-label="Commits"'),
                svg.index('aria-label="Total"'),
            )

        with t.subTest('the total fill sits under the type lines'):
            svg = t.ac.svg

            t.assertLess(
                svg.index('<polygon'),
                svg.index('aria-label="Commits"'),
            )
            t.assertLess(
                svg.index('aria-label="Issues"'),
                svg.index('aria-label="Total"'),
            )

        with t.subTest('legend'):
            svg = t.ac.svg

            t.assertIn('y="318"', svg)
            t.assertIn('>Total 8</text>', svg)
            t.assertIn('>Pull requests 2</text>', svg)

        with t.subTest('gridlines carry integer labels'):
            svg = t.ac.svg

            t.assertIn('<line x1="44" y1="266.0" x2="880" y2="266.0"', svg)
            t.assertIn('>0</text>', svg)
            t.assertIn('>6</text>', svg)
            t.assertIn('>8</text>', svg)

        with t.subTest('one day label in five'):
            days = {f'2026-08-0{day}': 0 for day in range(1, 8)}
            t.ac = ActivityChart(days, {name: days for name in SERIES})

            svg = t.ac.svg

            t.assertIn('>08-01<', svg)
            t.assertIn('>08-06<', svg)
            t.assertIn('>08-07<', svg)
            t.assertNotIn('>08-03<', svg)
