from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Book, Review, Report
from .serializers import ReviewSerializer
from django.http import JsonResponse

# Review List View (GET, POST)
class ReviewsListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, book_id):
        try:
            book = Book.objects.get(google_id=book_id)
        except Book.DoesNotExist:
            return Response({'error': 'Book not found'}, status=status.HTTP_404_NOT_FOUND)

        reviews = list(book.reviews.all()
                       .select_related('user__profile')
                       .prefetch_related('likes')
                       .order_by('-created_at'))
        if not reviews:
            return Response(
                {'message': 'No reviews available. Be the first one to add a review!'},
                status=status.HTTP_200_OK,
            )
        serializer = ReviewSerializer(reviews, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request, book_id):
        book_data = request.data.get('book')
        if not isinstance(book_data, dict):
            return Response({'error': 'Missing book details (google_id, title, authors)'},
                            status=status.HTTP_400_BAD_REQUEST)
        google_id = book_data.get('google_id')
        title = book_data.get('title')
        authors = book_data.get('authors')
        if not (google_id and title and authors) or not all(isinstance(v, str) for v in (google_id, title, authors)):
            return Response({'error': 'Missing book details (google_id, title, authors)'},
                            status=status.HTTP_400_BAD_REQUEST)

        book, _ = Book.objects.get_or_create(
            google_id=google_id[:255],
            defaults={'title': title[:200], 'authors': authors},
        )

        serializer = ReviewSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user, book=book)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Review Detail View (GET, PUT, DELETE)
class ReviewDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, review_id):
        try:
            review = Review.objects.get(id=review_id)
            # Pass context to the serializer
            serializer = ReviewSerializer(review, context={'request': request})
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Review.DoesNotExist:
            return Response({'error': 'Review not found'}, status=status.HTTP_404_NOT_FOUND)

    def put(self, request, review_id):
        try:
            review = Review.objects.get(id=review_id, user=request.user)
            # Pass context to the serializer
            serializer = ReviewSerializer(review, data=request.data, partial=True, context={'request': request})
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_200_OK)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Review.DoesNotExist:
            return Response({'error': 'Review not found or you are not authorized to edit this review'}, status=status.HTTP_404_NOT_FOUND)

    def delete(self, request, review_id):
        try:
            review = Review.objects.get(id=review_id, user=request.user)
            review.delete()
            return Response({'message': 'Review deleted successfully'}, status=status.HTTP_200_OK)
        except Review.DoesNotExist:
            return Response({'error': 'Review not found or you are not authorized to delete this review'}, status=status.HTTP_404_NOT_FOUND)

class LikeReviewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, review_id):
        try:
            review = Review.objects.get(id=review_id)
            user = request.user  # Get the logged-in user

            # Toggle like/unlike
            if user in review.likes.all():
                review.likes.remove(user)  # Remove the like
                message = 'Like removed'
            else:
                review.likes.add(user)  # Add the like
                message = 'Like added'
            review.save()

            # Return the actual likes count and the current like status
            has_liked = user in review.likes.all()  # Check if the user has liked the review
            return JsonResponse({
                'likes': review.likes.count(),
                'has_liked': has_liked,  # Send has_liked value back
                'message': message
            }, status=200)

        except Review.DoesNotExist:
            return JsonResponse({'error': 'Review not found'}, status=404)
        
class ReportReviewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, review_id):
        try:
            review = Review.objects.get(id=review_id)
            reason = request.data.get("reason")

            if not reason or not isinstance(reason, str):
                return JsonResponse({"error": "Reason for report is required"}, status=400)
            reason = reason.strip()[:255]

            # Create a report entry in the database
            report = Report.objects.create(
                review=review,
                user=request.user,
                reason=reason
            )

            # Optionally, return a success message
            return JsonResponse({"message": "Report submitted successfully"}, status=201)

        except Review.DoesNotExist:
            return JsonResponse({"error": "Review not found"}, status=404)

