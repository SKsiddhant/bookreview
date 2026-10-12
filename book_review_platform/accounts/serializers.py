from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Profile, Genre
import os

from django.core.exceptions import ValidationError as DjangoValidationError

from .images import detect_extension

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email']

class GenreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Genre
        fields = ['id','key', 'name']  # Return the id and name of genres

class SignUpSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['username', 'password', 'email']

    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)  # Create a user with hashed password
        return user

class ProfileSerializer(serializers.ModelSerializer):
    favorite_genres = serializers.SlugRelatedField(
        slug_field='key',  # Slug is stored in the 'key' field
        queryset=Genre.objects.all(),
        many=True
    )
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = Profile
        fields = ['bio', 'profile_pic', 'favorite_genres', 'username']

    def validate_profile_pic(self, value):
        if value is None:
            return value
        try:
            ext = detect_extension(value.read())
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        finally:
            value.seek(0)
        value.name = f'upload.{ext}'  # extension comes from the detected format, not the client
        return value

    def update(self, instance, validated_data):
        favorite_genres = validated_data.pop('favorite_genres', None)
        new_profile_pic = validated_data.get('profile_pic', None)

        if new_profile_pic:  # replace the picture
            if instance.profile_pic:
                try:
                    if os.path.isfile(instance.profile_pic.path):
                        os.remove(instance.profile_pic.path)  # delete the old file
                except (OSError, ValueError):
                    pass
            ext = new_profile_pic.name.rsplit('.', 1)[-1]
            instance.profile_pic.save(f"{instance.user.username}_profile.{ext}", new_profile_pic)

        instance.bio = validated_data.get('bio', instance.bio)
        instance.save()

        # Only touch the genres if the request included them; a partial update used to wipe them.
        if favorite_genres is not None:
            instance.favorite_genres.set(favorite_genres)

        return instance
