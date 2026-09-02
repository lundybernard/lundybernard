"""Render a daily contribution chart for a set of GitHub accounts.

The chart sums the contribution calendars of every account in LOGINS over the
last WINDOW_DAYS days. The GitHub API token comes from the GH_TOKEN
environment variable. The module uses the standard library only.
"""

from argparse import ArgumentParser
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from functools import cached_property
from json import dumps, loads
from math import ceil
from os import getenv
from pathlib import Path
from typing import NamedTuple
from urllib.request import Request, urlopen

LOGINS = ('lundybernard', '3M1LY-lb')
WINDOW_DAYS = 31

API_URL = 'https://api.github.com/graphql'
COMMIT_FIELD = 'commitContributionsByRepository'
COMMIT_LABEL = 'Commits'
ITEM_SERIES = (
    ('Pull requests', 'pullRequestContributions', 'prCursor'),
    ('Reviews', 'pullRequestReviewContributions', 'reviewCursor'),
    ('Issues', 'issueContributions', 'issueCursor'),
)
UNREAD_COMMITS = 'a repository holds more commits than one page'
QUERY = """
query(
  $login: String!
  $from: DateTime!
  $to: DateTime!
  $prCursor: String
  $reviewCursor: String
  $issueCursor: String
) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
      commitContributionsByRepository(maxRepositories: 100) {
        contributions(first: 100) {
          pageInfo { hasNextPage }
          nodes { occurredAt commitCount }
        }
      }
      pullRequestContributions(first: 100, after: $prCursor) {
        pageInfo { hasNextPage endCursor }
        nodes { occurredAt }
      }
      pullRequestReviewContributions(first: 100, after: $reviewCursor) {
        pageInfo { hasNextPage endCursor }
        nodes { occurredAt }
      }
      issueContributions(first: 100, after: $issueCursor) {
        pageInfo { hasNextPage endCursor }
        nodes { occurredAt }
      }
    }
  }
}
"""

WIDTH = 900
HEIGHT = 330
PAD_LEFT = 44
PAD_RIGHT = 20
PAD_TOP = 48
PAD_BOTTOM = 64
GRID_LINES = 4
LABEL_EVERY = 5
TITLE = 'Recent Contributions'
TOTAL_LABEL = 'Total'
TOTAL_COLOR = '#26a641'
SERIES_STYLE = {
    COMMIT_LABEL: ('#2a78d6', 'none'),
    'Pull requests': ('#d95926', 'none'),
    'Reviews': ('#8957e5', '5 3'),
    'Issues': ('#d55181', 'none'),
}
SWATCH = 18
SWATCH_GAP = 6
CHAR_WIDTH = 6.6
ENTRY_GAP = 26
MUTED = '#8b949e'
FONT = 'Segoe UI, Helvetica, Arial, sans-serif'

PLOT_WIDTH = WIDTH - PAD_LEFT - PAD_RIGHT
PLOT_HEIGHT = HEIGHT - PAD_TOP - PAD_BOTTOM
PLOT_BOTTOM = HEIGHT - PAD_BOTTOM
DAY_LABEL_Y = PLOT_BOTTOM + 22
LEGEND_Y = HEIGHT - 12


class Point(NamedTuple):
    """One plotted day: chart coordinates, ISO date and count."""

    x: float
    y: float
    day: str
    count: int


class ContributionCalendar:
    """Daily contribution counts of one GitHub account."""

    def __init__(
        self, login: str, start: date, end: date, token: str
    ) -> None:
        self.login = login
        self.start = start
        self.end = end
        self.token = token

    @cached_property
    def pages(self) -> tuple[dict, ...]:
        """Every contributionsCollection page of this account."""
        cursors: dict[str, str | None] = {
            variable: None for _, _, variable in ITEM_SERIES
        }
        pages = []
        while True:
            page = self._fetch(cursors)
            pages.append(page)
            unread = False
            for _, field, variable in ITEM_SERIES:
                info = page[field]['pageInfo']
                cursors[variable] = info['endCursor']
                unread = unread or info['hasNextPage']
            if not unread:
                return tuple(pages)

    @cached_property
    def series(self) -> dict[str, dict[str, int]]:
        """Daily count per contribution type."""
        counts = {COMMIT_LABEL: self._commit_counts()}
        for label, field, _ in ITEM_SERIES:
            days: Counter[str] = Counter()
            for page in self.pages:
                for node in page[field]['nodes']:
                    days[node['occurredAt'][:10]] += 1
            counts[label] = days
        return {label: dict(days) for label, days in counts.items()}

    def _fetch(self, cursors: Mapping[str, str | None]) -> dict:
        """Return one contributionsCollection page."""
        body = dumps(
            {
                'query': QUERY,
                'variables': {
                    'login': self.login,
                    'from': f'{self.start.isoformat()}T00:00:00Z',
                    'to': f'{self.end.isoformat()}T23:59:59Z',
                    **cursors,
                },
            }
        ).encode('utf-8')
        request = Request(
            API_URL,
            data=body,
            headers={
                'Authorization': f'bearer {self.token}',
                'Content-Type': 'application/json',
            },
        )
        with urlopen(request) as reply:
            response = loads(reply.read())
        return response['data']['user']['contributionsCollection']

    def _commit_counts(self) -> Counter:
        """Return the commit count per day of the first page.

        Raises
        ------
        RuntimeError
            A repository holds more commit contributions than one page.
        """
        days: Counter[str] = Counter()
        for repository in self.pages[0][COMMIT_FIELD]:
            contributions = repository['contributions']
            if contributions['pageInfo']['hasNextPage']:
                raise RuntimeError(UNREAD_COMMITS)
            for node in contributions['nodes']:
                days[node['occurredAt'][:10]] += node['commitCount']
        return days

    @cached_property
    def daily_counts(self) -> dict[str, int]:
        """Contribution count per ISO date.

        The response can include days outside the requested range.
        """
        weeks = self.pages[0]['contributionCalendar']['weeks']
        return {
            day['date']: day['contributionCount']
            for week in weeks
            for day in week['contributionDays']
        }


class ContributionHistory:
    """Daily contribution totals across several GitHub accounts."""

    def __init__(
        self,
        logins: Sequence[str],
        start: date,
        end: date,
        token: str,
    ) -> None:
        self.logins = logins
        self.start = start
        self.end = end
        self.token = token

    @cached_property
    def calendars(self) -> tuple[ContributionCalendar, ...]:
        """One calendar per account."""
        return tuple(
            ContributionCalendar(login, self.start, self.end, self.token)
            for login in self.logins
        )

    @cached_property
    def window(self) -> tuple[str, ...]:
        """Every ISO date of the window, in order."""
        span = (self.end - self.start).days + 1
        return tuple(
            (self.start + timedelta(days=offset)).isoformat()
            for offset in range(span)
        )

    @cached_property
    def series(self) -> dict[str, dict[str, int]]:
        """Daily count per type, summed over the accounts and zero filled."""
        counts: dict[str, Counter[str]] = {}
        for calendar in self.calendars:
            for label, days in calendar.series.items():
                counts.setdefault(label, Counter()).update(days)
        return {
            label: {day: days[day] for day in self.window}
            for label, days in counts.items()
        }

    @cached_property
    def daily_totals(self) -> dict[str, int]:
        """Summed count for every ISO date in the window, zero filled."""
        counts: Counter[str] = Counter()
        for calendar in self.calendars:
            counts.update(calendar.daily_counts)
        return {day: counts[day] for day in self.window}


class ActivityChart:
    """SVG line chart of daily contribution counts."""

    def __init__(
        self,
        daily_counts: Mapping[str, int],
        series: Mapping[str, Mapping[str, int]],
    ) -> None:
        self.daily_counts = daily_counts
        self.series = series

    @cached_property
    def series_points(self) -> dict[str, tuple[Point, ...]]:
        """Plotted days per contribution type, in draw order."""
        return {
            label: self._plot(self.series[label])
            for label in SERIES_STYLE
        }

    @cached_property
    def legend(self) -> tuple[tuple[str, int], ...]:
        """Label and window total per series, the daily total first."""
        entries = [(TOTAL_LABEL, sum(self.daily_counts.values()))]
        for label in SERIES_STYLE:
            entries.append((label, sum(self.series[label].values())))
        return tuple(entries)

    @cached_property
    def ceiling(self) -> int:
        """Top of the y axis.

        The value is a multiple of GRID_LINES. The value is never zero.
        """
        counts = list(self.daily_counts.values())
        for days in self.series.values():
            counts.extend(days.values())
        top = max(counts, default=0)
        return GRID_LINES * max(1, ceil(top / GRID_LINES))

    @cached_property
    def points(self) -> tuple[Point, ...]:
        """Plotted days of the daily total, in date order."""
        return self._plot(self.daily_counts)

    def _plot(self, counts: Mapping[str, int]) -> tuple[Point, ...]:
        """Map a day to count mapping onto chart coordinates."""
        days = sorted(counts.items())
        span = max(len(days) - 1, 1)
        return tuple(
            Point(
                PAD_LEFT + PLOT_WIDTH * index / span,
                PLOT_BOTTOM - PLOT_HEIGHT * count / self.ceiling,
                day,
                count,
            )
            for index, (day, count) in enumerate(days)
        )

    @cached_property
    def svg(self) -> str:
        """The chart as an SVG document."""
        elements = [
            f'<svg xmlns="http://www.w3.org/2000/svg"'
            f' viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}"'
            f' height="{HEIGHT}" role="img" aria-label="{TITLE}">',
            f'<text x="{PAD_LEFT}" y="27" fill="{MUTED}"'
            f' font-family="{FONT}" font-size="15">{TITLE}</text>',
            *self._gridlines(),
            self._total_fill(),
            *self._type_series(),
            *self._total_series(),
            *self._day_labels(),
            *self._legend(),
            '</svg>',
        ]
        return '\n'.join(elements) + '\n'

    def _gridlines(self) -> list[str]:
        """Return the horizontal rules and their y axis labels."""
        step = self.ceiling // GRID_LINES
        elements = []
        for index in range(GRID_LINES + 1):
            y = PLOT_BOTTOM - PLOT_HEIGHT * index / GRID_LINES
            elements.append(
                f'<line x1="{PAD_LEFT}" y1="{y:.1f}"'
                f' x2="{WIDTH - PAD_RIGHT}" y2="{y:.1f}"'
                f' stroke="{MUTED}" stroke-opacity="0.25"/>'
            )
            elements.append(
                f'<text x="{PAD_LEFT - 8}" y="{y + 4:.1f}" fill="{MUTED}"'
                f' font-family="{FONT}" font-size="11"'
                f' text-anchor="end">{step * index}</text>'
            )
        return elements

    def _track(self, points: Sequence[Point]) -> str:
        """Return the polyline coordinates of a series."""
        return ' '.join(f'{point.x:.1f},{point.y:.1f}' for point in points)

    def _type_series(self) -> list[str]:
        """Return one thin unfilled line per contribution type."""
        elements = []
        for label, (color, dash) in SERIES_STYLE.items():
            points = self.series_points[label]
            days = '\n'.join(
                f'{point.day}: {point.count}' for point in points
            )
            elements.extend(
                [
                    f'<g aria-label="{label}">',
                    f'<desc>{days}</desc>',
                    f'<polyline points="{self._track(points)}" fill="none"'
                    f' stroke="{color}" stroke-width="1.5"'
                    f' stroke-dasharray="{dash}" stroke-linecap="round"'
                    f' stroke-linejoin="round"/>',
                    '</g>',
                ]
            )
        return elements

    def _total_fill(self) -> str:
        """Return the area under the daily total line.

        The chart paints this area before the type lines.
        """
        return (
            f'<polygon points="{self.points[0].x:.1f},{PLOT_BOTTOM}'
            f' {self._track(self.points)}'
            f' {self.points[-1].x:.1f},{PLOT_BOTTOM}"'
            f' fill="{TOTAL_COLOR}" fill-opacity="0.18"/>'
        )

    def _total_series(self) -> list[str]:
        """Return the daily total line and its hover dots."""
        return [
            f'<g aria-label="{TOTAL_LABEL}">',
            f'<polyline points="{self._track(self.points)}" fill="none"'
            f' stroke="{TOTAL_COLOR}" stroke-width="2"'
            f' stroke-linecap="round" stroke-linejoin="round"/>',
            *self._markers(),
            '</g>',
        ]

    def _legend(self) -> list[str]:
        """Return a colored swatch and a label total for every series."""
        elements = []
        x = float(PAD_LEFT)
        for label, total in self.legend:
            color, dash = SERIES_STYLE.get(label, (TOTAL_COLOR, 'none'))
            text = f'{label} {total}'
            elements.append(
                f'<line x1="{x:.1f}" y1="{LEGEND_Y - 4}"'
                f' x2="{x + SWATCH:.1f}" y2="{LEGEND_Y - 4}"'
                f' stroke="{color}" stroke-width="2"'
                f' stroke-dasharray="{dash}"/>'
            )
            elements.append(
                f'<text x="{x + SWATCH + SWATCH_GAP:.1f}" y="{LEGEND_Y}"'
                f' fill="{MUTED}" font-family="{FONT}"'
                f' font-size="12">{text}</text>'
            )
            x += SWATCH + SWATCH_GAP + len(text) * CHAR_WIDTH + ENTRY_GAP
        return elements

    def _markers(self) -> list[str]:
        """Return one hoverable dot per day."""
        return [
            f'<circle cx="{point.x:.1f}" cy="{point.y:.1f}" r="2.5"'
            f' fill="{TOTAL_COLOR}"><title>{point.day}: {point.count}</title>'
            f'</circle>'
            for point in self.points
        ]

    def _day_labels(self) -> list[str]:
        """Return a label for one day in LABEL_EVERY, and for the last."""
        last = len(self.points) - 1
        return [
            f'<text x="{point.x:.1f}" y="{DAY_LABEL_Y}" fill="{MUTED}"'
            f' font-family="{FONT}" font-size="11"'
            f' text-anchor="middle">{point.day[5:]}</text>'
            for index, point in enumerate(self.points)
                if index % LABEL_EVERY == 0 or index == last
        ]


def main(argv: Sequence[str] | None = None) -> None:
    """Write the combined activity chart to the requested path."""
    parser = ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        '--out',
        default='activity.svg',
        help='path of the SVG file to write',
    )
    args = parser.parse_args(argv)

    end = datetime.now(timezone.utc).date()
    history = ContributionHistory(
        LOGINS,
        end - timedelta(days=WINDOW_DAYS - 1),
        end,
        getenv('GH_TOKEN', ''),
    )
    chart = ActivityChart(history.daily_totals, history.series)
    Path(args.out).write_text(chart.svg, encoding='utf-8')


if __name__ == '__main__':
    main()
