from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from ratings.models import Book
from .models import Report, Review


def client_for(username):
    user = User.objects.create_user(username, f'{username}@example.com', 'pw-Str0ng-123')
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')
    return user, c


BOOK = {'google_id': 'g1', 'title': 'T', 'authors': 'A'}


class ReviewTests(TestCase):
    def setUp(self):
        self.ua, self.a = client_for('a')
        self.ub, self.b = client_for('b')

    def post_review(self, client, content='Great', book=BOOK):
        return client.post('/reviews/g1/', {'content': content, 'book': book}, format='json')

    def test_auth_required(self):
        self.assertEqual(APIClient().get('/reviews/g1/').status_code, 401)

    def test_create_and_list(self):
        r = self.post_review(self.a)
        self.assertEqual(r.status_code, 201, r.content)
        data = self.b.get('/reviews/g1/').json()
        self.assertEqual(data[0]['content'], 'Great')
        self.assertEqual(data[0]['user']['username'], 'a')
        self.assertFalse(data[0]['has_liked'])

    def test_bad_payloads_are_400_and_do_not_leak(self):
        for book in (None, 'str', {'google_id': 1, 'title': 'T', 'authors': 'A'}, {'title': 'T'}):
            r = self.post_review(self.a, book=book)
            self.assertEqual(r.status_code, 400)
        self.assertEqual(self.post_review(self.a, content='').status_code, 400)
        self.assertEqual(self.post_review(self.a, content='x' * 5001).status_code, 400)
        self.assertEqual(Review.objects.count(), 0)

    def test_only_owner_can_edit_or_delete(self):
        self.post_review(self.a)
        rid = Review.objects.get().id
        self.assertEqual(self.b.put(f'/reviews/review/{rid}/', {'content': 'hijack'}, format='json').status_code, 404)
        self.assertEqual(self.b.delete(f'/reviews/review/{rid}/').status_code, 404)
        self.assertEqual(self.a.put(f'/reviews/review/{rid}/', {'content': 'edited'}, format='json').status_code, 200)
        self.assertEqual(self.a.delete(f'/reviews/review/{rid}/').status_code, 200)

    def test_like_toggle_and_has_liked(self):
        self.post_review(self.a)
        rid = Review.objects.get().id
        self.assertEqual(self.b.post(f'/reviews/like/{rid}/').json()['likes'], 1)
        self.assertTrue(self.b.get('/reviews/g1/').json()[0]['has_liked'])
        self.assertFalse(self.a.get('/reviews/g1/').json()[0]['has_liked'])
        self.assertEqual(self.b.post(f'/reviews/like/{rid}/').json()['likes'], 0)

    def test_report_requires_reason_and_truncates(self):
        self.post_review(self.a)
        rid = Review.objects.get().id
        self.assertEqual(self.b.post(f'/reviews/report/{rid}/', {}, format='json').status_code, 400)
        self.assertEqual(self.b.post(f'/reviews/report/{rid}/', {'reason': 'r' * 400}, format='json').status_code, 201)
        self.assertEqual(len(Report.objects.get().reason), 255)

    def test_list_uses_constant_queries(self):
        for i in range(5):
            u, c = client_for(f'u{i}')
            self.post_review(c, content=f'review {i}')
        with self.assertNumQueries(4):
            self.assertEqual(len(self.a.get('/reviews/g1/').json()), 5)
