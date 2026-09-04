package handler

import (
	"context"
	"errors"
	"net/http"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/multica-ai/multica/server/internal/service"
	"github.com/multica-ai/multica/server/internal/testutil"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

func inviteOnlyHandler() *Handler {
	h := *testHandler
	h.cfg.AllowSignup = false
	h.cfg.AllowedEmails = []string{"allowlisted@example.invalid"}
	h.cfg.AllowedEmailDomains = nil
	// No provider or SMTP transport: these tests must never send real email.
	h.EmailService = &service.EmailService{}
	return &h
}

func TestInviteOnlySignup(t *testing.T) {
	t.Setenv("APP_ENV", "production")
	for _, tc := range []struct {
		name, status        string
		duration            time.Duration
		wrongEmail, allowed bool
	}{
		{"pending", "pending", time.Hour, false, true},
		{"missing", "", time.Hour, false, false},
		{"expired", "pending", -time.Hour, false, false},
		{"accepted", "accepted", time.Hour, false, false},
		{"declined", "declined", time.Hour, false, false},
		{"different_email", "pending", time.Hour, true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			h := inviteOnlyHandler()
			email := "invite-only-" + tc.name + "@example.invalid"
			dbfx.Cleanup(t, `DELETE FROM "user" WHERE email = $1`, email)
			dbfx.Cleanup(t, "DELETE FROM verification_code WHERE email = $1", email)
			if tc.status != "" {
				inviteEmail := email
				if tc.wrongEmail {
					inviteEmail = "other-" + email
				}
				dbfx.Insert(t, "workspace_invitation", testutil.Cols{
					"workspace_id": testWorkspaceID, "inviter_id": testUserID,
					"invitee_email": inviteEmail, "role": "member", "status": tc.status,
					"expires_at": time.Now().Add(tc.duration),
				})
			}
			want := http.StatusForbidden
			if tc.allowed {
				want = http.StatusOK
			}
			testutil.Call(t, h.SendCode, newRequest("POST", "/auth/send-code", SendCodeRequest{Email: email})).Want(want)
			if tc.allowed {
				code, err := h.Queries.GetLatestVerificationCode(context.Background(), email)
				if err != nil {
					t.Fatal(err)
				}
				wrong := "000000"
				if wrong == code.Code {
					wrong = "111111"
				}
				testutil.Call(t, h.VerifyCode, newRequest("POST", "/auth/verify-code", VerifyCodeRequest{Email: email, Code: wrong})).Want(http.StatusBadRequest)
				if n := dbfx.Count(t, `SELECT count(*) FROM "user" WHERE email = $1`, email); n != 0 {
					t.Fatal("wrong code created an account")
				}
				testutil.Call(t, h.VerifyCode, newRequest("POST", "/auth/verify-code", VerifyCodeRequest{Email: email, Code: code.Code})).Want(http.StatusOK)
				user, err := h.Queries.GetUserByEmail(context.Background(), email)
				if err != nil {
					t.Fatal(err)
				}
				if n := dbfx.Count(t, "SELECT count(*) FROM member WHERE user_id = $1", user.ID); n != 0 {
					t.Fatal("signup must not automatically grant membership")
				}
			} else {
				// Even a valid previously issued code cannot bypass invitation eligibility.
				dbfx.Insert(t, "verification_code", testutil.Cols{"email": email, "code": "314159", "expires_at": time.Now().Add(time.Hour)})
				testutil.Call(t, h.VerifyCode, newRequest("POST", "/auth/verify-code", VerifyCodeRequest{Email: email, Code: "314159"})).Want(http.StatusForbidden)
				if n := dbfx.Count(t, `SELECT count(*) FROM "user" WHERE email = $1`, email); n != 0 {
					t.Fatal("ineligible signup created an account")
				}
			}
		})
	}
}

func TestInviteOnlySignupRechecksInvitation(t *testing.T) {
	for _, action := range []string{"revoke", "expire"} {
		t.Run(action, func(t *testing.T) {
			h := inviteOnlyHandler()
			email := "invite-only-recheck-" + action + "@example.invalid"
			dbfx.Cleanup(t, `DELETE FROM "user" WHERE email = $1`, email)
			dbfx.Cleanup(t, "DELETE FROM verification_code WHERE email = $1", email)
			id := dbfx.Insert(t, "workspace_invitation", testutil.Cols{
				"workspace_id": testWorkspaceID, "inviter_id": testUserID, "invitee_email": email,
				"role": "member", "status": "pending", "expires_at": time.Now().Add(time.Hour),
			})
			testutil.Call(t, h.SendCode, newRequest("POST", "/auth/send-code", SendCodeRequest{Email: email})).Want(http.StatusOK)
			code, err := h.Queries.GetLatestVerificationCode(context.Background(), email)
			if err != nil {
				t.Fatal(err)
			}
			if action == "revoke" {
				dbfx.Exec(t, "DELETE FROM workspace_invitation WHERE id = $1", id)
			} else {
				dbfx.Exec(t, "UPDATE workspace_invitation SET expires_at = now() - interval '1 hour' WHERE id = $1", id)
			}
			testutil.Call(t, h.VerifyCode, newRequest("POST", "/auth/verify-code", VerifyCodeRequest{Email: email, Code: code.Code})).Want(http.StatusForbidden)
			if n := dbfx.Count(t, `SELECT count(*) FROM "user" WHERE email = $1`, email); n != 0 {
				t.Fatal("withdrawn invitation created an account")
			}
		})
	}
}

type invitationQueryFailure struct{ mockDB }

func (*invitationQueryFailure) Query(context.Context, string, ...any) (pgx.Rows, error) {
	return nil, errors.New("invitation database unavailable")
}

func TestInviteOnlySignupQueryFailure(t *testing.T) {
	h := inviteOnlyHandler()
	h.Queries = db.New(&invitationQueryFailure{mockDB{getUserErr: pgx.ErrNoRows}})
	testutil.Call(t, h.SendCode, newRequest("POST", "/auth/send-code", SendCodeRequest{Email: "new@example.invalid"})).Want(http.StatusInternalServerError)
	if _, _, err := h.findOrCreateUser(context.Background(), "new@example.invalid"); err == nil {
		t.Fatal("invitation lookup failure must fail closed")
	}
}
