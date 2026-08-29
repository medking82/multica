package handler

import (
	"context"
	"net/http"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgtype"
	"github.com/multica-ai/multica/server/internal/service"
	"github.com/multica-ai/multica/server/internal/testutil"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
	"github.com/multica-ai/multica/server/pkg/protocol"
)

func TestSelectedSlashSkillIDs(t *testing.T) {
	markdown := make([]string, 0, maxSelectedSlashSkillsPerTask+2)
	markdown = append(markdown, "[/first](slash://skill/a) [/again](slash://skill/a)")
	for i := 0; i < maxSelectedSlashSkillsPerTask+1; i++ {
		markdown = append(markdown, "[/skill](slash://skill/id-"+strings.Repeat("x", i+1)+")")
	}

	ids := selectedSlashSkillIDs(markdown...)
	if len(ids) != maxSelectedSlashSkillsPerTask {
		t.Fatalf("selected ids count = %d, want cap %d", len(ids), maxSelectedSlashSkillsPerTask)
	}
	if ids[0] != "a" {
		t.Fatalf("first-occurrence order lost: %v", ids)
	}
}

func TestSelectedSlashSkillIDsForClaimTrustsOnlyMemberComments(t *testing.T) {
	task := db.AgentTaskQueue{
		TriggerCommentID:    pgtype.UUID{Valid: true},
		CoalescedCommentIds: []pgtype.UUID{{Valid: true}, {Valid: true}},
	}
	resp := AgentTaskResponse{
		TriggerAuthorType:     "agent",
		TriggerCommentContent: "[/agent](slash://skill/agent-skill)",
		CoalescedComments: []CoalescedCommentData{
			{AuthorType: "system", Content: "[/system](slash://skill/system-skill)"},
			{AuthorType: "member", Content: "[/member](slash://skill/member-skill)"},
		},
	}

	ids := selectedSlashSkillIDsForClaim(task, resp)
	if len(ids) != 1 || ids[0] != "member-skill" {
		t.Fatalf("selected ids = %v, want only member-authored marker", ids)
	}
}

func TestMergeTaskSkillsDoesNotMutateConfiguredBackingArray(t *testing.T) {
	configured := make([]service.AgentSkillData, 2, 4)
	configured[0] = service.AgentSkillData{ID: "configured-1"}
	configured[1] = service.AgentSkillData{ID: "configured-2"}
	selected := []service.AgentSkillData{{ID: "selected-1"}}

	merged := mergeTaskSkills(configured, selected)
	if len(merged) != 3 {
		t.Fatalf("merged count = %d, want 3", len(merged))
	}
	if configured[:cap(configured)][2].ID != "" {
		t.Fatal("mergeTaskSkills wrote selected data into configured backing array")
	}
}

func TestClaimTaskByRuntime_SelectedWorkspaceSkillGrant(t *testing.T) {
	if testHandler == nil || testPool == nil {
		t.Skip("database not available")
	}

	ctx := context.Background()
	runtimeID := createClaimReclaimRuntime(t, ctx, "Selected Skill runtime")
	agentID, issueID := createClaimReclaimAgentAndIssue(t, ctx, runtimeID, "Selected Skill agent")
	selectedID := dbfx.Insert(t, "skill", testutil.Cols{
		"workspace_id": testWorkspaceID,
		"name":         "selected-workspace-skill",
		"description":  "Selected only for this run",
		"content":      "selected content",
		"config":       testutil.Raw("'{}'::jsonb"),
		"created_by":   testUserID,
	})
	unselectedID := dbfx.Insert(t, "skill", testutil.Cols{
		"workspace_id": testWorkspaceID,
		"name":         "unselected-workspace-skill",
		"description":  "Must remain unavailable",
		"content":      "unselected content",
		"config":       testutil.Raw("'{}'::jsonb"),
		"created_by":   testUserID,
	})
	triggerID := dbfx.Comment(
		t,
		issueID,
		"please [/selected-workspace-skill](slash://skill/"+selectedID+")",
	)
	taskID := dbfx.Task(t, agentID, testutil.Cols{
		"runtime_id":         runtimeID,
		"issue_id":           issueID,
		"trigger_comment_id": triggerID,
	})

	req := newDaemonTokenRequest(
		http.MethodPost,
		"/api/daemon/runtimes/"+runtimeID+"/tasks/claim",
		nil,
		testWorkspaceID,
		"selected-skill-daemon",
	)
	req.Header.Set("X-Client-Capabilities", protocol.DaemonCapabilitySkillBundlesV1)
	req = withURLParam(req, "runtimeId", runtimeID)
	w := testutil.Call(t, testHandler.ClaimTaskByRuntime, req).Want(http.StatusOK)

	var claim struct {
		Task *AgentTaskResponse `json:"task"`
	}
	w.JSON(&claim)
	if claim.Task == nil || claim.Task.Agent == nil {
		t.Fatalf("missing claimed task Agent: %s", w.Body.String())
	}
	var selectedRef service.AgentSkillRefData
	for _, ref := range claim.Task.Agent.SkillRefs {
		if ref.ID == selectedID {
			selectedRef = ref
		}
		if ref.ID == unselectedID {
			t.Fatalf("unselected workspace Skill leaked into claim: %+v", ref)
		}
	}
	if selectedRef.ID == "" || selectedRef.Hash == "" {
		t.Fatalf("selected workspace Skill missing from claim refs: %+v", claim.Task.Agent.SkillRefs)
	}

	var persistedContext string
	dbfx.QueryRow(t, `SELECT context::text FROM agent_task_queue WHERE id = $1`, taskID).Scan(&persistedContext)
	if !strings.Contains(persistedContext, selectedID) || strings.Contains(persistedContext, unselectedID) {
		t.Fatalf("persisted selected-Skill grant is not exact: %s", persistedContext)
	}

	resolveSelected := resolveSkillBundlesRequest{Skills: []resolveSkillBundleRef{{
		ID: selectedRef.ID, Source: selectedRef.Source, Hash: selectedRef.Hash,
	}}}
	req = newDaemonTokenRequest(
		http.MethodPost,
		"/api/daemon/runtimes/"+runtimeID+"/tasks/"+taskID+"/skill-bundles/resolve",
		resolveSelected,
		testWorkspaceID,
		"selected-skill-daemon",
	)
	req = withURLParams(req, "runtimeId", runtimeID, "taskId", taskID)
	testutil.Call(t, testHandler.ResolveTaskSkillBundles, req).Want(http.StatusOK)

	resolveUnselected := resolveSkillBundlesRequest{Skills: []resolveSkillBundleRef{{
		ID: unselectedID, Source: selectedRef.Source, Hash: selectedRef.Hash,
	}}}
	req = newDaemonTokenRequest(
		http.MethodPost,
		"/api/daemon/runtimes/"+runtimeID+"/tasks/"+taskID+"/skill-bundles/resolve",
		resolveUnselected,
		testWorkspaceID,
		"selected-skill-daemon",
	)
	req = withURLParams(req, "runtimeId", runtimeID, "taskId", taskID)
	testutil.Call(t, testHandler.ResolveTaskSkillBundles, req).Want(http.StatusNotFound)
}
