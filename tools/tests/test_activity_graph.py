from datetime import date
from json import loads
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
                t.ContributionHistory.return_value.daily_totals
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


class ContributionCalendarTests(TestCase):
    """Unit tests for tools.activity_graph.ContributionCalendar."""

    def setUp(t) -> None:  # pylint: disable=arguments-renamed
        for target in ('Request', 'urlopen'):
            patcher = patch(f'{SRC}.{target}', autospec=True)
            setattr(t, target, patcher.start())
            t.addCleanup(patcher.stop)
        reply = t.urlopen.return_value.__enter__.return_value
        reply.read.return_value = b'{"data": {"user": null}}'
        t.cc = ContributionCalendar(
            'alpha',
            date(2026, 8, 31),
            date(2026, 9, 2),
            'TOKEN',
        )

    def test_response(t) -> None:
        t.assertEqual(t.cc.response, {'data': {'user': None}})

        t.urlopen.assert_called_once_with(t.Request.return_value)
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
            },
        )
        t.assertIn('contributionCalendar', body['query'])

    def test_daily_counts(t) -> None:
        t.cc.response = {
            'data': {
                'user': {
                    'contributionsCollection': {
                        'contributionCalendar': {
                            'weeks': [
                                {
                                    'contributionDays': [
                                        {
                                            'date': '2026-08-31',
                                            'contributionCount': 4,
                                        },
                                        {
                                            'date': '2026-09-01',
                                            'contributionCount': 0,
                                        },
                                    ]
                                },
                                {
                                    'contributionDays': [
                                        {
                                            'date': '2026-09-02',
                                            'contributionCount': 7,
                                        },
                                    ]
                                },
                            ]
                        }
                    }
                }
            }
        }

        t.assertEqual(
            t.cc.daily_counts,
            {'2026-08-31': 4, '2026-09-01': 0, '2026-09-02': 7},
        )


class ActivityChartTests(TestCase):
    """Unit tests for tools.activity_graph.ActivityChart."""

    def setUp(t) -> None:  # pylint: disable=arguments-renamed
        t.ac = ActivityChart(
            {'2026-09-02': 0, '2026-08-31': 6, '2026-09-01': 2}
        )

    def test_ceiling(t) -> None:
        with t.subTest('rounds up to a multiple of the gridline count'):
            t.assertEqual(t.ac.ceiling, 8)

        with t.subTest('an exact multiple stands'):
            t.ac = ActivityChart({'2026-08-31': 44})

            t.assertEqual(t.ac.ceiling, 44)

        with t.subTest('no contributions'):
            t.ac = ActivityChart({'2026-08-31': 0})

            t.assertEqual(t.ac.ceiling, 4)

    def test_points(t) -> None:
        t.assertEqual(
            t.ac.points,
            (
                Point(44.0, 102.5, '2026-08-31', 6),
                Point(462.0, 211.5, '2026-09-01', 2),
                Point(880.0, 266.0, '2026-09-02', 0),
            ),
        )

    def test_svg(t) -> None:
        with t.subTest('document'):
            svg = t.ac.svg

            t.assertTrue(svg.startswith('<svg '), svg[:40])
            t.assertTrue(svg.endswith('</svg>\n'), svg[-40:])
            t.assertIn('viewBox="0 0 900 300"', svg)
            t.assertIn('>Recent Contributions<', svg)
            t.assertIn('#26a641', svg)
            t.assertIn('points="44.0,102.5 462.0,211.5 880.0,266.0"', svg)
            t.assertIn('<title>2026-08-31: 6</title>', svg)
            t.assertIn('<title>2026-09-02: 0</title>', svg)
            t.assertIn('>08-31<', svg)
            t.assertIn('>09-02<', svg)

        with t.subTest('gridlines carry integer labels'):
            svg = t.ac.svg

            t.assertIn('<line x1="44" y1="266.0" x2="880" y2="266.0"', svg)
            t.assertIn('>0</text>', svg)
            t.assertIn('>6</text>', svg)
            t.assertIn('>8</text>', svg)

        with t.subTest('one day label in five'):
            t.ac = ActivityChart(
                {f'2026-08-0{day}': 0 for day in range(1, 8)}
            )

            svg = t.ac.svg

            t.assertIn('>08-01<', svg)
            t.assertIn('>08-06<', svg)
            t.assertIn('>08-07<', svg)
            t.assertNotIn('>08-03<', svg)
