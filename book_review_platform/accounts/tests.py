import base64
import re
from io import BytesIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Genre

STRONG = 'Tr0ub4dor&3-horse'


def png_data_url(fmt='PNG'):
    buf = BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buf, fmt)
    return f'data:image/{fmt.lower()};base64,' + base64.b64encode(buf.getvalue()).decode()


def make_user(username='alice', email='alice@example.com', verified=True, complete=True, active=True):
    user = User.objects.create_user(username=username, email=email, password=STRONG)
    user.is_active = active
    user.save()
    profile = user.profile
    profile.is_verified = verified
    profile.profile_complete = complete
    profile.save()
    return user


def auth(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')
    return client


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FRONTEND_URL='http://front.test')
class AccountFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def post(self, url, payload):
        return self.client.post(url, payload, format='json')

    # ---- signup ----
    def test_signup_creates_inactive_user_and_sends_link_to_configured_frontend(self):
        r = self.post('/accounts/signup/', {'username': 'bob', 'email': 'Bob@Example.com', 'password': STRONG})
        self.assertEqual(r.status_code, 201)
        user = User.objects.get(username='bob')
        self.assertFalse(user.is_active)
        self.assertEqual(user.email, 'bob@example.com')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('http://front.test/verify-email/', mail.outbox[0].alternatives[0][0])

    def test_signup_rejects_weak_password_and_duplicate_email_ignoring_case(self):
        r = self.post('/accounts/signup/', {'username': 'bob', 'email': 'b@example.com', 'password': '12345678'})
        self.assertEqual(r.status_code, 400)
        make_user()
        r = self.post('/accounts/signup/', {'username': 'other', 'email': 'ALICE@example.com', 'password': STRONG})
        self.assertEqual(r.status_code, 400)

    def test_signup_survives_smtp_failure_without_leaking_details(self):
        with self.settings(EMAIL_BACKEND='nonexistent.backend.Boom'), self.assertLogs('accounts.views', 'ERROR'):
            r = self.post('/accounts/signup/', {'username': 'bob', 'email': 'b@example.com', 'password': STRONG})
        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.json()['email_sent'])

    def test_malformed_body_is_400_not_500(self):
        r = self.client.post('/accounts/login/', 'not json', content_type='application/json')
        self.assertEqual(r.status_code, 400)

    # ---- login ----
    def test_unverified_user_gets_verification_pending_not_invalid_password(self):
        make_user('carol', 'c@example.com', verified=False, complete=False, active=False)
        r = self.post('/accounts/login/', {'username': 'carol', 'password': STRONG})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.json()['verification_pending'])

    def test_wrong_password_is_401(self):
        make_user()
        r = self.post('/accounts/login/', {'username': 'alice', 'password': 'nope'})
        self.assertEqual(r.status_code, 401)

    def test_verified_complete_user_gets_tokens(self):
        make_user()
        r = self.post('/accounts/login/', {'username': 'alice', 'password': STRONG})
        self.assertEqual(r.status_code, 200)
        self.assertIn('access', r.json()['token'])

    def test_profile_incomplete_is_reported(self):
        make_user(complete=False)
        r = self.post('/accounts/login/', {'username': 'alice', 'password': STRONG})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(r.json()['profile_complete'])

    # ---- verification ----
    def _link_for(self, user):
        self.post('/accounts/resend-verification/', {'username': user.username})
        html = mail.outbox[-1].alternatives[0][0]
        return re.search(r'/verify-email/([^/]+)/([^/"]+)/', html).groups()

    def test_verify_email_marks_profile_and_returns_token_until_setup_is_done(self):
        user = make_user('dave', 'd@example.com', verified=False, complete=False, active=False)
        uid, token = self._link_for(user)
        r = self.client.get(f'/accounts/verify-email/{uid}/{token}/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('token', r.json())
        user.refresh_from_db(); user.profile.refresh_from_db()
        self.assertTrue(user.is_active and user.profile.is_verified)

        user.profile.profile_complete = True
        user.profile.save()
        r = self.client.get(f'/accounts/verify-email/{uid}/{token}/')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('token', r.json())  # a replayed link is not a password-less login

    def test_verify_email_bad_token(self):
        user = make_user(verified=False, active=False)
        r = self.client.get(f'/accounts/verify-email/abc/not-a-token/')
        self.assertEqual(r.status_code, 400)

    def test_resend_is_rate_limited(self):
        make_user('erin', 'e@example.com', verified=False, complete=False, active=False)
        self.assertEqual(self.post('/accounts/resend-verification/', {'username': 'erin'}).status_code, 200)
        self.assertEqual(self.post('/accounts/resend-verification/', {'username': 'erin'}).status_code, 429)
        self.assertEqual(len(mail.outbox), 1)

    # ---- password reset ----
    def test_forgot_password_does_not_reveal_whether_email_exists(self):
        make_user()
        known = self.post('/accounts/forgot-password/', {'email': 'alice@example.com'})
        unknown = self.post('/accounts/forgot-password/', {'email': 'nobody@example.com'})
        self.assertEqual((known.status_code, known.json()), (unknown.status_code, unknown.json()))
        self.assertEqual(len(mail.outbox), 1)  # only the real account got mail

    def test_forgot_password_cooldown(self):
        make_user()
        self.post('/accounts/forgot-password/', {'email': 'alice@example.com'})
        self.post('/accounts/forgot-password/', {'email': 'alice@example.com'})
        self.assertEqual(len(mail.outbox), 1)

    def test_reset_confirm_enforces_password_rules_and_works_once(self):
        user = make_user()
        self.post('/accounts/forgot-password/', {'email': 'alice@example.com'})
        uid, token = re.search(r'/reset-password/([^/]+)/([^/"]+)/', mail.outbox[0].alternatives[0][0]).groups()
        url = f'/accounts/reset-password/{uid}/{token}/'
        self.assertEqual(self.post(url, {'password': 'short'}).status_code, 400)
        self.assertEqual(self.post(url, {}).status_code, 400)
        self.assertEqual(self.post(url, {'password': 'An0ther-Good-One!'}).status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password('An0ther-Good-One!'))
        self.assertEqual(self.post(url, {'password': 'Yet-An0ther-One!'}).status_code, 400)  # link is spent


class AuthenticatedAccountTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client = auth(self.user)

    def test_change_password_requires_valid_new_password(self):
        r = self.client.put('/accounts/change-password/', {'current_password': STRONG}, format='json')
        self.assertEqual(r.status_code, 400)  # used to call set_password(None) and lock the account
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_usable_password())
        self.assertTrue(self.user.check_password(STRONG))

        r = self.client.put('/accounts/change-password/', {'current_password': STRONG, 'new_password': '123'}, format='json')
        self.assertEqual(r.status_code, 400)
        r = self.client.put('/accounts/change-password/', {'current_password': 'wrong', 'new_password': 'Another-Good-1!'}, format='json')
        self.assertEqual(r.status_code, 400)
        r = self.client.put('/accounts/change-password/', {'current_password': STRONG, 'new_password': 'Another-Good-1!'}, format='json')
        self.assertEqual(r.status_code, 200)

    def test_change_username_validation(self):
        make_user('taken', 't@example.com')
        for bad in (None, '', '   ', 'has space', 'TAKEN', 'x' * 151):
            r = self.client.put('/accounts/change-username/', {'new_username': bad}, format='json')
            self.assertEqual(r.status_code, 400, bad)
        r = self.client.put('/accounts/change-username/', {'new_username': 'alice2'}, format='json')
        self.assertEqual(r.status_code, 200)

    def test_endpoints_require_authentication(self):
        anon = APIClient()
        for method, url in (('put', '/accounts/change-password/'), ('put', '/accounts/change-username/'),
                            ('delete', '/accounts/delete-account/'), ('get', '/accounts/profile/'),
                            ('post', '/accounts/profile/setup/')):
            self.assertEqual(getattr(anon, method)(url).status_code, 401, url)


class ProfileSetupTests(TestCase):
    def setUp(self):
        self.user = make_user('frank', 'f@example.com', verified=True, complete=False)
        self.other = make_user('gina', 'g@example.com', verified=True, complete=False)
        self.genre = Genre.objects.get_or_create(key='fantasy', defaults={'name': 'Fantasy'})[0]
        self.client = auth(self.user)

    def test_setup_is_anonymous_proof_and_cannot_target_another_user(self):
        payload = {'username': 'gina', 'bio': 'hijack', 'favoriteGenres': [self.genre.id]}
        self.assertEqual(APIClient().post('/accounts/profile/setup/', payload, format='json').status_code, 401)
        r = self.client.post('/accounts/profile/setup/', payload, format='json')  # frank sends gina's name
        self.assertEqual(r.status_code, 200)
        self.other.profile.refresh_from_db(); self.user.profile.refresh_from_db()
        self.assertFalse(self.other.profile.profile_complete)
        self.assertEqual(self.user.profile.bio, 'hijack')  # applied to the caller, not the named user
        self.assertEqual(list(self.user.profile.favorite_genres.all()), [self.genre])

    def test_unverified_user_cannot_complete_setup(self):
        self.user.profile.is_verified = False
        self.user.profile.save()
        r = self.client.post('/accounts/profile/setup/', {'bio': 'x'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_valid_picture_is_stored_with_detected_extension(self):
        r = self.client.post('/accounts/profile/setup/', {'bio': 'hi', 'profilePic': png_data_url()}, format='json')
        self.assertEqual(r.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.profile_pic.name.endswith('.png'))
        self.user.profile.profile_pic.delete(save=False)

    def test_non_image_payloads_are_rejected(self):
        evil = 'data:text/html;base64,' + base64.b64encode(b'<script>alert(1)</script>').decode()
        for pic in (evil, 'data:image/png;base64,' + base64.b64encode(b'<script>alert(1)</script>').decode(), 'nonsense'):
            r = self.client.post('/accounts/profile/setup/', {'bio': 'hi', 'profilePic': pic}, format='json')
            self.assertEqual(r.status_code, 400, pic[:40])
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.profile_complete)

    def test_bad_genres_are_rejected_not_500(self):
        r = self.client.post('/accounts/profile/setup/', {'favoriteGenres': ['a']}, format='json')
        self.assertEqual(r.status_code, 400)


class ProfileUpdateTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.g1 = Genre.objects.get_or_create(key='scifi', defaults={'name': 'Sci-fi'})[0]
        self.g2 = Genre.objects.get_or_create(key='horror', defaults={'name': 'Horror'})[0]
        self.user.profile.favorite_genres.set([self.g1])
        self.client = auth(self.user)

    def test_json_genre_list_does_not_500(self):
        r = self.client.put('/accounts/profile/', {'favorite_genres': ['horror'], 'bio': 'b'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([g.key for g in self.user.profile.favorite_genres.all()], ['horror'])

    def test_partial_update_without_genres_keeps_them(self):
        r = self.client.put('/accounts/profile/', {'bio': 'new bio'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual([g.key for g in self.user.profile.favorite_genres.all()], ['scifi'])

    def test_username_cannot_be_changed_through_profile(self):
        self.client.put('/accounts/profile/', {'username': 'hacker'}, format='json')
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'alice')

    def test_invalid_genre_json_string_is_400(self):
        r = self.client.put('/accounts/profile/', {'favorite_genres': '[oops'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_uploaded_html_disguised_as_image_is_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        fake = SimpleUploadedFile('x.html', b'<script>alert(1)</script>', content_type='image/png')
        r = self.client.put('/accounts/profile/', {'profile_pic': fake}, format='multipart')
        self.assertEqual(r.status_code, 400)
