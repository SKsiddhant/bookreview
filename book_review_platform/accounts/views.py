import json
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.http import JsonResponse, QueryDict
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .images import image_from_data_url
from .models import Genre, Profile
from .serializers import GenreSerializer, ProfileSerializer
from .throttle import allow_once

User = get_user_model()
logger = logging.getLogger(__name__)

EMAIL_COOLDOWN_SECONDS = 60


def _json_body(request):
    """Parse a JSON object body or return None."""
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _bad_request(message, status_code=400, **extra):
    return JsonResponse({'error': message, **extra}, status=status_code)


def _server_error(exc):
    """Log the real problem server-side; never send exception text to the client."""
    logger.exception('Unhandled error in account view: %s', exc)
    return JsonResponse({'error': 'Something went wrong. Please try again.'}, status=500)


def _password_errors(password, user=None):
    try:
        validate_password(password, user=user)
    except ValidationError as exc:
        return ' '.join(exc.messages)
    return None


@csrf_exempt  # JSON API authenticated by JWT, no cookie session is used here
def signup_view(request):
    if request.method != 'POST':
        return _bad_request('Invalid request method', 405)
    data = _json_body(request)
    if data is None:
        return _bad_request('Invalid JSON body')
    try:
        username = str(data.get('username') or '').strip()
        email = str(data.get('email') or '').strip().lower()
        password = data.get('password')

        if not username or not email or not password or not isinstance(password, str):
            return _bad_request('All fields are required')
        try:
            UnicodeUsernameValidator()(username)
            if len(username) > 150:
                raise ValidationError('too long')
            validate_email(email)
        except ValidationError:
            return _bad_request('Enter a valid username (letters, digits and @/./+/-/_ only) and e-mail address')

        if User.objects.filter(username__iexact=username).exists():
            return _bad_request('Username already exists')
        if User.objects.filter(email__iexact=email).exists():
            return _bad_request('E-mail ID already exists')

        problem = _password_errors(password, User(username=username, email=email))
        if problem:
            return _bad_request(problem)

        user = User.objects.create_user(username=username, email=email, password=password)
        user.is_active = False  # cannot log in until the e-mail is verified
        user.save()

        email_sent = True
        try:
            send_verification_email(user)
        except Exception:  # SMTP down / misconfigured: the account exists, the user can resend
            logger.exception('Could not send verification e-mail to user %s', user.pk)
            email_sent = False

        return JsonResponse({
            'message': 'User registered successfully! Please verify your e-mail.'
                       if email_sent else
                       'User registered, but the verification e-mail could not be sent. Use "resend" on the login page.',
            'email_sent': email_sent,
        }, status=201)
    except Exception as exc:
        return _server_error(exc)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([JSONParser])
def profile_setup_view(request):
    """Finish the profile of the *authenticated* user.

    This endpoint used to be public and took a username from the body, so anyone could complete
    (and overwrite) another person's profile.
    """
    user = request.user
    profile = user.profile
    if not profile.is_verified:
        return Response({'error': 'Verify your e-mail first.'}, status=status.HTTP_403_FORBIDDEN)
    if profile.profile_complete:
        return Response({'error': 'Profile setup already completed.'}, status=status.HTTP_400_BAD_REQUEST)

    data = request.data
    bio = data.get('bio')
    favorite_genres = data.get('favoriteGenres') or []
    profile_pic_data = data.get('profilePic')

    if bio is not None and (not isinstance(bio, str) or len(bio) > 255):
        return Response({'error': 'Bio must be text of at most 255 characters.'}, status=400)
    if not isinstance(favorite_genres, list) or not all(isinstance(g, int) for g in favorite_genres):
        return Response({'error': 'favoriteGenres must be a list of genre ids.'}, status=400)

    pic = None
    if profile_pic_data:
        try:
            pic = image_from_data_url(profile_pic_data, f'{user.username}_profile')
        except ValidationError as exc:
            return Response({'error': ' '.join(exc.messages)}, status=400)

    if bio is not None:
        profile.bio = bio
    if pic is not None:
        profile.profile_pic = pic
    profile.profile_complete = True
    profile.save()
    profile.favorite_genres.set(Genre.objects.filter(id__in=favorite_genres))

    return Response({'message': 'Profile updated successfully and account activated!'}, status=200)


@csrf_exempt
def login_view(request):
    if request.method != 'POST':
        return _bad_request('Invalid request method', 405)
    data = _json_body(request)
    if data is None:
        return _bad_request('Invalid JSON body')
    try:
        username = data.get('username')
        password = data.get('password')
        if not username or not password or not isinstance(username, str) or not isinstance(password, str):
            return _bad_request('Username and password are required')

        # authenticate() refuses inactive users, which made the "verify your e-mail" branch below
        # unreachable (unverified accounts are inactive). Check the password ourselves instead.
        user = User.objects.filter(username=username).first()
        if user is None or not user.check_password(password):
            return _bad_request('Invalid username or password', 401)

        profile = user.profile
        if not profile.is_verified:
            return JsonResponse({
                'error': 'Account not verified. Would you like to resend the verification e-mail?',
                'verification_pending': True,
                'is_verified': False,
            }, status=403)

        if not user.is_active or not profile.profile_complete:
            return JsonResponse({
                'error': 'Profile setup incomplete. Redirecting...',
                'profile_complete': False,
                'is_verified': profile.is_verified,
            }, status=403)

        refresh = RefreshToken.for_user(user)
        return JsonResponse({
            'message': 'Login successful!',
            'token': {'refresh': str(refresh), 'access': str(refresh.access_token)},
            'user': {
                'username': user.username,
                'email': user.email,
                'profile_complete': profile.profile_complete,
                'is_verified': profile.is_verified,
            },
        }, status=200)
    except Exception as exc:
        return _server_error(exc)


class ProfileView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, JSONParser]  # file uploads and JSON

    def get(self, request):
        profile = get_object_or_404(Profile, user=request.user)
        return Response(ProfileSerializer(profile).data, status=status.HTTP_200_OK)

    def put(self, request):
        request_data = request.data.dict() if isinstance(request.data, QueryDict) else dict(request.data)

        # multipart sends favorite_genres as a JSON string, JSON requests send a real list
        genres = request_data.get('favorite_genres')
        if isinstance(genres, str):
            try:
                request_data['favorite_genres'] = json.loads(genres)
            except ValueError:
                return Response({'favorite_genres': ['Invalid list.']}, status=status.HTTP_400_BAD_REQUEST)

        profile = get_object_or_404(Profile, user=request.user)
        serializer = ProfileSerializer(profile, data=request_data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class GenreViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Genre.objects.all()
    serializer_class = GenreSerializer
    permission_classes = [IsAuthenticated]


class ProfileViewSet(viewsets.ModelViewSet):
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):  # users may only see/modify their own profile
        return Profile.objects.filter(user=self.request.user)


def genres_list_view(request):
    if request.method == 'GET':
        return JsonResponse(GenreSerializer(Genre.objects.all(), many=True).data, safe=False)
    return _bad_request('Invalid request method', 405)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_favorite_genres(request):
    profile = get_object_or_404(Profile, user=request.user)
    names = profile.favorite_genres.all().values_list('name', flat=True)
    return JsonResponse({'favoriteGenres': list(names)}, status=200)


def send_verification_email(user):
    """E-mail the user a link to the React app that confirms their address."""
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    verification_link = f"{settings.FRONTEND_URL}/verify-email/{uid}/{token}/"

    html_message = render_to_string('registration/email_verification_email.html', {
        'username': user.username,
        'verification_link': verification_link,
        'current_year': timezone.now().year,
    })
    send_mail(
        'Please verify your e-mail address',
        '',
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
        html_message=html_message,
    )


def _user_from_uid(uidb64):
    try:
        return User.objects.get(pk=urlsafe_base64_decode(uidb64).decode())
    except (TypeError, ValueError, OverflowError, User.DoesNotExist, UnicodeDecodeError):
        return None


def verify_email(request, uidb64, token):
    user = _user_from_uid(uidb64)
    if user is None or not default_token_generator.check_token(user, token):
        return _bad_request('Invalid or expired verification link.')

    profile = user.profile
    profile.is_verified = True
    profile.save()
    user.is_active = True
    user.save()

    payload = {'message': 'Your e-mail has been verified successfully!', 'username': user.username}
    # Hand out a sign-in token only while the profile still has to be set up. Afterwards the
    # link no longer works as a password-less login for the rest of its validity window.
    if not profile.profile_complete:
        payload['token'] = str(RefreshToken.for_user(user).access_token)
    return JsonResponse(payload)


@csrf_exempt
def resend_verification_email(request):
    if request.method != 'POST':
        return _bad_request('Invalid request method', 405)
    data = _json_body(request)
    if data is None:
        return _bad_request('Invalid JSON body')
    try:
        username = data.get('username')
        if not username or not isinstance(username, str):
            return _bad_request('Username is required')

        user = User.objects.filter(username=username).first()
        if user is None:
            return _bad_request('User does not exist', 404)
        if user.profile.is_verified:
            return JsonResponse({'message': 'Your account is already verified.'}, status=200)
        if not allow_once(f'verify:{user.pk}', EMAIL_COOLDOWN_SECONDS):
            return _bad_request('A verification e-mail was just sent. Please wait a minute and check your inbox.', 429)

        send_verification_email(user)
        return JsonResponse({'message': 'Verification e-mail sent successfully!'}, status=200)
    except Exception as exc:
        return _server_error(exc)


@csrf_exempt
def forgot_password_view(request):
    if request.method != 'POST':
        return _bad_request('Invalid request method', 405)
    data = _json_body(request)
    if data is None:
        return _bad_request('Invalid JSON body')
    try:
        email = str(data.get('email') or '').strip().lower()
        if not email:
            return _bad_request('Email is required')

        # Same answer whether or not the address is registered, so this endpoint cannot be used to
        # find out who has an account; a cool-down stops it being used to flood an inbox.
        generic = JsonResponse({'message': 'If that e-mail is registered, a reset link has been sent.'}, status=200)
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is None or not allow_once(f'reset:{user.pk}', EMAIL_COOLDOWN_SECONDS):
            return generic

        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        reset_link = f'{settings.FRONTEND_URL}/reset-password/{uid}/{token}/'
        html_message = render_to_string('email/password_reset_email.html', {
            'username': user.username,
            'reset_link': reset_link,
            'current_year': timezone.now().year,
        })
        send_mail('Password Reset Request', '', settings.DEFAULT_FROM_EMAIL, [user.email],
                  fail_silently=False, html_message=html_message)
        return generic
    except Exception as exc:
        return _server_error(exc)


@csrf_exempt
def password_reset_confirm(request, uidb64, token):
    user = _user_from_uid(uidb64)
    if user is None or not default_token_generator.check_token(user, token):
        return _bad_request('The reset link is invalid or has expired.')

    if request.method == 'POST':
        data = _json_body(request)
        new_password = data.get('password') if data else None
        if not new_password or not isinstance(new_password, str):
            return _bad_request('Password is required.')
        problem = _password_errors(new_password, user)
        if problem:
            return _bad_request(problem)
        user.set_password(new_password)  # also invalidates this reset link
        user.save()
        return JsonResponse({'message': 'Your password has been reset successfully!'}, status=200)

    return JsonResponse({'message': 'Ready to reset password.'}, status=200)


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def change_password(request):
    user = request.user
    current_password = request.data.get('current_password')
    new_password = request.data.get('new_password')

    if not isinstance(current_password, str) or not user.check_password(current_password):
        return Response({'error': 'Current password is incorrect'}, status=status.HTTP_400_BAD_REQUEST)
    # A missing new_password used to reach set_password(None), which makes the account unusable.
    if not isinstance(new_password, str) or not new_password:
        return Response({'error': 'New password is required'}, status=status.HTTP_400_BAD_REQUEST)
    problem = _password_errors(new_password, user)
    if problem:
        return Response({'error': problem}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(new_password)
    user.save()
    return Response({'message': 'Password updated successfully'}, status=status.HTTP_200_OK)


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def change_username(request):
    user = request.user
    new_username = request.data.get('new_username')
    if not isinstance(new_username, str) or not new_username.strip():
        return Response({'error': 'A new username is required'}, status=status.HTTP_400_BAD_REQUEST)
    new_username = new_username.strip()
    try:
        UnicodeUsernameValidator()(new_username)
        if len(new_username) > 150:
            raise ValidationError('too long')
    except ValidationError:
        return Response({'error': 'Username may contain letters, digits and @/./+/-/_ only (150 characters max)'},
                        status=status.HTTP_400_BAD_REQUEST)
    if User.objects.filter(username__iexact=new_username).exclude(pk=user.pk).exists():
        return Response({'error': 'Username already taken'}, status=status.HTTP_400_BAD_REQUEST)

    user.username = new_username
    user.save()
    return Response({'message': 'Username updated successfully'}, status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_account(request):
    request.user.delete()
    return Response({'message': 'Account deleted successfully'}, status=status.HTTP_200_OK)
