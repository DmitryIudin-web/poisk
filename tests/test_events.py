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

    def test_same_vin_in_two_offers_is_stored_twice_but_alerted_once(self) -> None:
        first = replace(matching_listing(), vin="LSV2A2CA1PN123456")
        second = replace(
            first,
            listing_id="2",
            url="https://auto.drom.ru/moscow/volkswagen/teramont/2.html",
        )

        next_state, events, _ = apply_scan(MonitorState(), successful([first, second]), NOW)

        self.assertEqual(len(next_state.listings), 2)
        self.assertEqual([event.kind for event in events], ["new_relevant"])

    def test_new_offer_alerts_when_old_offer_with_same_vin_becomes_irrelevant(self) -> None:
        vin = "LSV2A2CA1PN123456"
        old = replace(matching_listing(), vin=vin)
        old_now_irrelevant = replace(old, exterior_black=Evidence(False, "белый"))
        new_offer = replace(
            old,
            listing_id="2",
            url="https://auto.drom.ru/moscow/volkswagen/teramont/2.html",
        )

        _, events, _ = apply_scan(state_with(old), successful([old_now_irrelevant, new_offer]), NOW)

        self.assertEqual([event.kind for event in events], ["new_relevant"])
        self.assertEqual(events[0].listing_key, "drom:2")

    def test_new_offer_stays_silent_when_old_offer_with_same_vin_remains_relevant(self) -> None:
        vin = "LSV2A2CA1PN123456"
        old = replace(matching_listing(), vin=vin)
        new_offer = replace(
            old,
            listing_id="2",
            url="https://auto.drom.ru/moscow/volkswagen/teramont/2.html",
        )

        _, events, _ = apply_scan(state_with(old), successful([new_offer, old]), NOW)

        self.assertEqual(events, [])

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

    def test_irrelevant_becoming_relevant_emits_new_relevant(self) -> None:
        irrelevant = replace(matching_listing(), exterior_black=Evidence(False, "белый"))

        _, events, _ = apply_scan(state_with(irrelevant), successful([matching_listing()]), NOW)

        self.assertEqual([event.kind for event in events], ["new_relevant"])

    def test_candidate_vin_confirmation_is_significant_before_full_relevance(self) -> None:
        candidate = replace(matching_listing(), dcc=Evidence(None, None), vin=None)
        with_vin = replace(candidate, vin="LSV2A2CA1PN123456")

        _, events, _ = apply_scan(state_with(candidate), successful([with_vin]), NOW)

        self.assertEqual([event.kind for event in events], ["critical_confirmation"])
        self.assertEqual(events[0].detail["confirmed"], ["vin"])

    def test_price_drop_boundary_is_inclusive(self) -> None:
        old = state_with(replace(matching_listing(), cash_price=6_000_000, price_currency="RUB"))
        current = replace(matching_listing(), cash_price=5_950_000, price_currency="RUB")

        _, events, _ = apply_scan(old, successful([current]), NOW)

        self.assertEqual([event.kind for event in events], ["price_drop"])
        self.assertEqual(events[0].detail["drop"], 50_000)

    def test_same_price_drop_can_alert_again_after_a_later_price_cycle(self) -> None:
        old = state_with(replace(matching_listing(), cash_price=6_000_000, price_currency="RUB"))
        dropped = replace(matching_listing(), cash_price=5_950_000, price_currency="RUB")
        first_state, first_events, _ = apply_scan(old, successful([dropped]), NOW)
        raised = replace(matching_listing(), cash_price=6_000_000, price_currency="RUB")
        raised_state, _, _ = apply_scan(first_state, successful([raised]), "2026-08-28T13:00:00Z")
        _, second_events, _ = apply_scan(raised_state, successful([dropped]), "2026-08-28T14:00:00Z")

        self.assertNotEqual(first_events[0].id, second_events[0].id)

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

    def test_partial_source_success_does_not_increment_misses(self) -> None:
        old = state_with(misses=1)
        partial = {
            "drom": SourceResult(
                "drom",
                True,
                (),
                search_url="https://auto.drom.ru/search",
                complete=False,
                warnings=(SourceGap("drom", "detail_failed", "one card failed"),),
            )
        }

        next_state, events, _ = apply_scan(old, partial, NOW)

        key = next(iter(next_state.listings))
        self.assertEqual(next_state.listings[key].misses, 1)
        self.assertEqual(events, [])

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

    def test_explicit_sold_does_not_alert_for_previously_irrelevant_listing(self) -> None:
        irrelevant = replace(matching_listing(), exterior_black=Evidence(False, "белый"))
        sold = replace(irrelevant, sold=Evidence(True, "продан"))

        _, events, _ = apply_scan(state_with(irrelevant), successful([sold]), NOW)

        self.assertEqual(events, [])

    def test_previously_sold_candidate_becoming_relevant_is_confirmation(self) -> None:
        candidate = replace(matching_listing(), dcc=Evidence(None, None), sold=Evidence(True, "продан"))
        current = replace(matching_listing(), sold=Evidence(False, None))

        _, events, _ = apply_scan(state_with(candidate, removed=True), successful([current]), NOW)

        self.assertEqual([event.kind for event in events], ["critical_confirmation"])

    def test_reappearance_after_removal_emits_became_available(self) -> None:
        old = state_with(removed=True)
        _, events, _ = apply_scan(old, successful([matching_listing()]), NOW)
        self.assertEqual([event.kind for event in events], ["became_available"])

    def test_stock_change_to_available_emits_became_available_not_new(self) -> None:
        unavailable = replace(matching_listing(), in_stock=Evidence(False, "в пути"))

        _, events, _ = apply_scan(state_with(unavailable), successful([matching_listing()]), NOW)

        self.assertEqual([event.kind for event in events], ["became_available"])

    def test_second_stock_availability_cycle_has_a_new_event_id(self) -> None:
        unavailable = replace(matching_listing(), in_stock=Evidence(False, "в пути"))
        first_state, first_events, _ = apply_scan(state_with(unavailable), successful([matching_listing()]), NOW)
        unavailable_again, _, _ = apply_scan(
            first_state, successful([unavailable]), "2026-08-28T13:00:00Z"
        )
        _, second_events, _ = apply_scan(
            unavailable_again, successful([matching_listing()]), "2026-08-28T14:00:00Z"
        )

        self.assertNotEqual(first_events[0].id, second_events[0].id)

    def test_second_removal_cycle_has_a_new_event_id(self) -> None:
        first_absence, _, _ = apply_scan(state_with(), successful(), NOW)
        removed, first_removal, _ = apply_scan(first_absence, successful(), "2026-08-28T13:00:00Z")
        reappeared, _, _ = apply_scan(removed, successful([matching_listing()]), "2026-08-28T14:00:00Z")
        second_absence, _, _ = apply_scan(reappeared, successful(), "2026-08-28T15:00:00Z")
        _, second_removal, _ = apply_scan(second_absence, successful(), "2026-08-28T16:00:00Z")

        self.assertNotEqual(first_removal[0].id, second_removal[0].id)

    def test_known_event_id_is_not_emitted_twice(self) -> None:
        _, first, _ = apply_scan(MonitorState(), successful([matching_listing()]), NOW)
        _, second, _ = apply_scan(
            MonitorState(), successful([matching_listing()]), NOW, known_event_ids={first[0].id}
        )
        self.assertEqual(second, [])


if __name__ == "__main__":
    unittest.main()
