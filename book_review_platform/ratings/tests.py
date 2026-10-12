from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


def client_for(username):
    user = User.objects.create_user(username, f'{username}@example.com', 'pw-Str0ng-123')
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')
    return c


class RatingTests(TestCase):
    def test_requires_auth(self):
        self.assertEqual(APIClient().post('/ratings/rate-book/', {}, format='json').status_code, 401)
        self.assertEqual(APIClient().get('/ratings/get-book-ratings/?bookId=x').status_code, 401)

    def test_rate_update_and_average(self):
        a, b = client_for('a'), client_for('b')
        body = {'bookId': 'g1', 'rating': 4, 'title': 'T', 'authors': 'A'}
        self.assertEqual(a.post('/ratings/rate-book/', body, format='json').status_code, 201)
        self.assertEqual(a.post('/ratings/rate-book/', {**body, 'rating': 2}, format='json').status_code, 200)
        b.post('/ratings/rate-book/', {**body, 'rating': 5}, format='json')
        data = a.get('/ratings/get-book-ratings/?bookId=g1').json()
        self.assertEqual(data['average_rating'], 3.5)
        self.assertEqual(len(data['ratings']), 2)

    def test_invalid_ratings_are_400(self):
        c = client_for('a')
        for bad in ('nan', 'inf', -1, 5.1, 'abc', [1], {'x': 1}):
            r = c.post('/ratings/rate-book/', {'bookId': 'g', 'rating': bad}, format='json')
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(c.get('/ratings/get-book-ratings/?bookId=g').status_code, 404)  # nothing stored
