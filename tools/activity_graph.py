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
PRIVATE_FIELD = 'restrictedContributionsCount'
CONTRIBUTION_TYPES = (
    ('Commits', 'totalCommitContributions'),
    ('Pull requests', 'totalPullRequestContributions'),
    ('Reviews', 'totalPullRequestReviewContributions'),
    ('Issues', 'totalIssueContributions'),
    ('Repos created', 'totalRepositoryContributions'),
    ('Private', PRIVATE_FIELD),
)
QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalIssueContributions
      totalRepositoryContributions
      restrictedContributionsCount
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
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
ACCENT = '#26a641'
MUTED = '#8b949e'
FONT = 'Segoe UI, Helvetica, Arial, sans-serif'

PLOT_WIDTH = WIDTH - PAD_LEFT - PAD_RIGHT
PLOT_HEIGHT = HEIGHT - PAD_TOP - PAD_BOTTOM
PLOT_BOTTOM = HEIGHT - PAD_BOTTOM
DAY_LABEL_Y = PLOT_BOTTOM + 22
BREAKDOWN_Y = HEIGHT - 12


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
    def response(self) -> dict:
        """Decoded GraphQL response for this account."""
        body = dumps(
            {
                'query': QUERY,
                'variables': {
                    'login': self.login,
                    'from': f'{self.start.isoformat()}T00:00:00Z',
                    'to': f'{self.end.isoformat()}T23:59:59Z',
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
            return loads(reply.read())

    @cached_property
    def collection(self) -> dict:
        """The contributionsCollection object of the response."""
        return self.response['data']['user']['contributionsCollection']

    @cached_property
    def totals(self) -> dict[str, int]:
        """Contribution count per type over the window."""
        return {
            field: self.collection[field]
            for _, field in CONTRIBUTION_TYPES
        }

    @cached_property
    def daily_counts(self) -> dict[str, int]:
        """Contribution count per ISO date.

        The response can include days outside the requested range.
        """
        weeks = self.collection['contributionCalendar']['weeks']
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
    def totals(self) -> dict[str, int]:
        """Contribution count per type, summed over the accounts."""
        counts: Counter[str] = Counter()
        for calendar in self.calendars:
            counts.update(calendar.totals)
        return dict(counts)

    @cached_property
    def daily_totals(self) -> dict[str, int]:
        """Summed count for every ISO date in the window, zero filled."""
        counts: Counter[str] = Counter()
        for calendar in self.calendars:
            counts.update(calendar.daily_counts)
        span = (self.end - self.start).days + 1
        window = (
            (self.start + timedelta(days=offset)).isoformat()
            for offset in range(span)
        )
        return {day: counts[day] for day in window}


class ActivityChart:
    """SVG line chart of daily contribution counts."""

    def __init__(
        self,
        daily_counts: Mapping[str, int],
        totals: Mapping[str, int],
    ) -> None:
        self.daily_counts = daily_counts
        self.totals = totals

    @cached_property
    def breakdown(self) -> str:
        """The contribution summary line under the chart.

        The line omits a private count of zero.
        """
        entries = [f'Total {sum(self.daily_counts.values())}']
        for label, field in CONTRIBUTION_TYPES:
            count = self.totals[field]
            if field == PRIVATE_FIELD and not count:
                continue
            entries.append(f'{label} {count}')
        return ' · '.join(entries)

    @cached_property
    def ceiling(self) -> int:
        """Top of the y axis.

        The value is a multiple of GRID_LINES. The value is never zero.
        """
        top = max(self.daily_counts.values(), default=0)
        return GRID_LINES * max(1, ceil(top / GRID_LINES))

    @cached_property
    def points(self) -> tuple[Point, ...]:
        """Plotted days, in date order."""
        days = sorted(self.daily_counts.items())
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
        track = ' '.join(
            f'{point.x:.1f},{point.y:.1f}' for point in self.points
        )
        edges = (
            f'{self.points[0].x:.1f},{PLOT_BOTTOM}',
            f'{self.points[-1].x:.1f},{PLOT_BOTTOM}',
        )
        elements = [
            f'<svg xmlns="http://www.w3.org/2000/svg"'
            f' viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}"'
            f' height="{HEIGHT}" role="img" aria-label="{TITLE}">',
            f'<text x="{PAD_LEFT}" y="27" fill="{MUTED}"'
            f' font-family="{FONT}" font-size="15">{TITLE}</text>',
            *self._gridlines(),
            f'<polygon points="{edges[0]} {track} {edges[1]}"'
            f' fill="{ACCENT}" fill-opacity="0.18"/>',
            f'<polyline points="{track}" fill="none" stroke="{ACCENT}"'
            f' stroke-width="2" stroke-linecap="round"'
            f' stroke-linejoin="round"/>',
            *self._markers(),
            *self._day_labels(),
            f'<text x="{PAD_LEFT}" y="{BREAKDOWN_Y}" fill="{MUTED}"'
            f' font-family="{FONT}" font-size="12">{self.breakdown}</text>',
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

    def _markers(self) -> list[str]:
        """Return one hoverable dot per day."""
        return [
            f'<circle cx="{point.x:.1f}" cy="{point.y:.1f}" r="2.5"'
            f' fill="{ACCENT}"><title>{point.day}: {point.count}</title>'
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
    chart = ActivityChart(history.daily_totals, history.totals)
    Path(args.out).write_text(chart.svg, encoding='utf-8')


if __name__ == '__main__':
    main()
