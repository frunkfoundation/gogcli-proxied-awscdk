import unittest

from routing import RoutingError, resolve_google_host_and_path


class GoogleRoutingTests(unittest.TestCase):
    def test_supported_services_preserve_original_host_and_path(self):
        routes = {
            "www.googleapis.com": "/drive/v3/files",
            "gmail.googleapis.com": "/gmail/v1/users/me/messages",
            "calendar.googleapis.com": "/calendar/v3/calendars/primary/events",
            "tasks.googleapis.com": "/tasks/v1/users/@me/lists",
            "sheets.googleapis.com": "/v4/spreadsheets/id",
            "docs.googleapis.com": "/v1/documents/id",
            "slides.googleapis.com": "/v1/presentations/id",
            "forms.googleapis.com": "/v1/forms/id",
            "script.googleapis.com": "/v1/projects/id",
            "chat.googleapis.com": "/v1/spaces/id",
            "classroom.googleapis.com": "/v1/courses/id",
            "people.googleapis.com": "/v1/people/me",
            "keep.googleapis.com": "/v1/notes/id",
            "cloudidentity.googleapis.com": "/v1/groups/id",
            "admin.googleapis.com": "/admin/directory/v1/users",
            "adsense.googleapis.com": "/v2/accounts",
            "analyticsadmin.googleapis.com": "/v1beta/accounts",
            "analyticsdata.googleapis.com": "/v1beta/properties/id:runReport",
            "meet.googleapis.com": "/v2/spaces/id",
            "driveactivity.googleapis.com": "/v2/activity:query",
            "drivelabels.googleapis.com": "/v2/labels",
            "searchconsole.googleapis.com": "/webmasters/v3/sites",
            "youtube.googleapis.com": "/youtube/v3/channels",
            "photoslibrary.googleapis.com": "/v1/mediaItems",
            "photospicker.googleapis.com": "/v1/mediaItems",
            "maps.googleapis.com": "/maps/api/geocode/json",
            "places.googleapis.com": "/v1/places/id",
            "docs.google.com": "/document/d/id/export",
        }
        for host, path in routes.items():
            with self.subTest(host=host):
                self.assertEqual((host, path), resolve_google_host_and_path(path, host))

    def test_upload_and_escaped_paths_are_preserved(self):
        for path in ("/upload/drive/v3/files", "/resumable/upload/drive/v3/files", "/calendar/v3/calendars/a%2Fb/events"):
            self.assertEqual(("www.googleapis.com", path), resolve_google_host_and_path(path, "www.googleapis.com"))

    def test_relative_path_and_host_case_are_normalized(self):
        self.assertEqual(("gmail.googleapis.com", "/gmail/v1/users/me/labels"), resolve_google_host_and_path("gmail/v1/users/me/labels", "GMAIL.GOOGLEAPIS.COM"))

    def test_picker_media_accepts_only_valid_googleusercontent_subdomains(self):
        for host in ("lh3.googleusercontent.com", "media-1.photos.googleusercontent.com"):
            self.assertEqual((host, "/media=d"), resolve_google_host_and_path("/media=d", host))
        for host in ("googleusercontent.com", "evilgoogleusercontent.com", "lh3.googleusercontent.com.evil.test", "-media.googleusercontent.com", "media_.googleusercontent.com", "media..googleusercontent.com", "a" * 64 + ".googleusercontent.com", "lh3.googleusercontent.com:443", "lh3.googleusercontent.com@evil.test"):
            with self.subTest(host=host), self.assertRaises(RoutingError):
                resolve_google_host_and_path("/media=d", host)

    def test_host_is_required_and_exact_allowlist_prevents_arbitrary_destinations(self):
        for host in ("", "example.com", "evil.googleapis.com", "gmail.googleapis.com.evil.test", "gmail.googleapis.com:443", "https://gmail.googleapis.com", "gmail.googleapis.com@evil.test", "gmail.googleapis.com/", " gmail.googleapis.com", "gmail.googleapis.com\n"):
            with self.subTest(host=host), self.assertRaises(RoutingError):
                resolve_google_host_and_path("/gmail/v1/users/me/labels", host)

    def test_path_cannot_change_url_authority_or_embed_query_fragment_controls(self):
        for path in ("", "/", "//evil.test/path", "/v1/path?key=value", "/v1/path#fragment", "/v1/path\n", "/v1/path\x00"):
            with self.subTest(path=path), self.assertRaises(RoutingError):
                resolve_google_host_and_path(path, "gmail.googleapis.com")


if __name__ == "__main__":
    unittest.main()
