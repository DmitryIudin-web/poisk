from __future__ import annotations

import unittest
from dataclasses import replace

from teramont_monitor.events import apply_scan
from teramont_monitor.identity import listing_key
from teramont_monitor.models import Evidence, ListingState, MonitorState, SourceGap, SourceResult
from teramont_monitor.qualify import qualify
from tests.test_qualify import matching_listing


NOW = "2026-08-28T12:00:00Z"


def state_with(listing=None, *, misses: int = 0, removed: bool = False) -> MonitorState:
    listing = listing or matching_listing()
    status, missing = qualify(listing)
    key = listing_key(listing)
    return MonitorState(
        listings={key: ListingState(listing, status, missing, misses, "2026-08-28T11:00:00Z", removed)}
    )


def successful(listings=()) -> dict[str, SourceResult]:
    return {"drom": SourceResult("drom", True, tuple(listings), search_url="https://auto.drom.ru/search")}


class EventTransitionTests(unittest.TestCase):
    def test_first_fully_relevant_listing_emits_new_relevant(self) -> None:
        _, events, _ = apply_scan(MonitorState(), successful([matching_listing()]), NOW)
        self.assertEqual([event.kind for event in events], ["new_relevant"])

    def test_initial_candidate_is_stored_without_event(self) -> None:
        candidate = replace(matching_listing(), dcc=Evidence(None, None))
        next_state, events, _ = apply_scan(MonitorState(), successful([candidate]), NOW)

        self.assertEqual(events, [])
        self.assertEqual(next_state.listings[listing_key(candidate)].status, "candidate")

    def test_candidate_becoming_relevant_emits_critical_confirmation(self) -> None:
        candidate = replace(matching_listing(), dcc=Evidence(None, None))
        old = state_with(candidate)

        _, events, _ = apply_scan(old, successful([matching_listing()]), NOW)

        self.assertEqual([event.kind for event in events], ["critical_confirmation"])
        self.assertEqual(events[0].detail["confirmed"], ["dcc"])

    def test_price_drop_boundary_is_inclusive(self) -> None:
        old = state_with(replace(matching_listing(), cash_price=6_000_000, price_currency="RUB"))
        current = replace(matching_listing(), cash_price=5_950_000, price_currency="RUB")

        _, events, _ = apply_scan(old, successful([current]), NOW)

        self.assertEqual([event.kind for event in events], ["price_drop"])
        self.assertEqual(events[0].detail["drop"], 50_000)

    def test_smaller_drop_and_price_increase_are_silent(self) -> None:
        old = state_with(replace(matching_listing(), cash_price=6_000_000, price_currency="RUB"))
        for price in (5_950_001, 6_100_000):
            with self.subTest(price=price):
                current = replace(matching_listing(), cash_price=price, price_currency="RUB")
                _, events, _ = apply_scan(old, successful([current]), NOW)
                self.assertEqual(events, [])

    def test_failed_source_does_not_increment_misses(self) -> None:
        old = state_with(misses=1)
        failed = {
            "drom": SourceResult(
                "drom", False, gap=SourceGap("drom", "blocked", "captcha"), search_url="https://auto.drom.ru/search"
            )
        }

        next_state, events, history = apply_scan(old, failed, NOW)

        key = next(iter(next_state.listings))
        self.assertEqual(next_state.listings[key].misses, 1)
        self.assertEqual(events, [])
        self.assertEqual(history[0]["type"], "source_gap")

    def test_two_successful_absences_emit_removed(self) -> None:
        first_state, first_events, _ = apply_scan(state_with(), successful(), NOW)
        second_state, second_events, _ = apply_scan(
            first_state, successful(), "2026-08-28T13:00:00Z"
        )

        key = next(iter(second_state.listings))
        self.assertEqual(first_events, [])
        self.assertEqual([event.kind for event in second_events], ["removed_or_sold"])
        self.assertTrue(second_state.listings[key].removed)

    def test_explicit_sold_emits_immediately(self) -> None:
        sold = replace(matching_listing(), sold=Evidence(True, "продан"))
        _, events, _ = apply_scan(state_with(), successful([sold]), NOW)
        self.assertEqual([event.kind for event in events], ["removed_or_sold"])

    def test_reappearance_after_removal_emits_became_available(self) -> None:
        old = state_with(removed=True)
        _, events, _ = apply_scan(old, successful([matching_listing()]), NOW)
        self.assertEqual([event.kind for event in events], ["became_available"])

    def test_known_event_id_is_not_emitted_twice(self) -> None:
        _, first, _ = apply_scan(MonitorState(), successful([matching_listing()]), NOW)
        _, second, _ = apply_scan(
            MonitorState(), successful([matching_listing()]), NOW, known_event_ids={first[0].id}
        )
        self.assertEqual(second, [])


if __name__ == "__main__":
    unittest.main()
