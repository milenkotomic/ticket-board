# Spec: shared notes

A tiny notes service. Users can create notes, share a note with another user
by email, and see notes shared with them.

## Requirements

1. A user creates a note with a title and a markdown body. Notes are private by default.
2. The owner shares a note with another registered user by email. The other
   user gets read-only access. Sharing with an unknown email returns an error.
3. A user lists "Shared with me", newest first, 20 per page.
4. The owner can revoke a share; the note disappears from the other user's list.
5. Rename the `body` column to `content` everywhere. Three clients read it today:
   the web app, the iOS app and the nightly export job.

## Out of scope

Real-time collaboration, comments, edit permissions for shared users.
