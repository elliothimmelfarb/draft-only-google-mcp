import pytest

from draft_only_google_mcp import server


def test_real_id_shapes_are_accepted(google):
    server.get_event("abc_20260101T100000Z", "en.usa#holiday@group.v.calendar.google.com")
    server.get_event("abc", "a.b+c@example.com")
    server.update_draft("r-123", "a@b.com", "body")
    assert google.urls[0].endswith("/calendars/en.usa%23holiday@group.v.calendar.google.com/events/abc_20260101T100000Z")


@pytest.mark.parametrize("call,payload", [
    (lambda: server.search_threads(), {"threads": [{"id": "../drafts/send?"}]}),
    (lambda: server.list_drafts(), {"drafts": [{"id": "../drafts/send?"}]}),
])
def test_ids_that_come_back_from_a_listing_are_checked_too(google, call, payload):
    google.payload = payload
    with pytest.raises(ValueError):
        call()
    assert len(google.urls) == 1
