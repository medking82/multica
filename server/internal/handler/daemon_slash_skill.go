package handler

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"

	"github.com/jackc/pgx/v5/pgtype"
	"github.com/multica-ai/multica/server/internal/service"
	"github.com/multica-ai/multica/server/internal/util"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
	"github.com/multica-ai/multica/server/pkg/skillbundle"
	"github.com/multica-ai/multica/server/pkg/slashskill"
)

// Matches the editor's display cap. The server still owns this ceiling so a
// hand-written Markdown payload cannot inflate a claim with unbounded Skills.
const maxSelectedSlashSkillsPerTask = slashskill.MaxSelectedPerPayload

func selectedSlashSkillIDs(markdowns ...string) []string {
	ids := make([]string, 0, maxSelectedSlashSkillsPerTask)
	seen := make(map[string]struct{}, maxSelectedSlashSkillsPerTask)
	for _, markdown := range markdowns {
		for _, ref := range slashskill.Extract(markdown) {
			if _, ok := seen[ref.ID]; ok {
				continue
			}
			seen[ref.ID] = struct{}{}
			ids = append(ids, ref.ID)
			if len(ids) == maxSelectedSlashSkillsPerTask {
				return ids
			}
		}
	}
	return ids
}

// selectedSlashSkillIDsForClaim reads only immutable/task-owned chat input or
// comment bodies actually admitted into this claim. Legacy chat tasks have no
// durable input owner, so they retain the existing attached-Skills behavior.
func selectedSlashSkillIDsForClaim(task db.AgentTaskQueue, resp AgentTaskResponse) []string {
	markdowns := make([]string, 0, len(resp.CoalescedComments)+3)
	if task.ChatInputTaskID.Valid {
		markdowns = append(markdowns, resp.ChatMessage)
	}
	// Quick-create's prompt is immutable, server-owned task context submitted
	// by the accountable member. It is the exact create-task payload this run
	// is claiming, so its slash markers have the same authority as chat input.
	if resp.QuickCreatePrompt != "" {
		markdowns = append(markdowns, resp.QuickCreatePrompt)
	}
	// A slash marker grants executable task authority, so only attributable
	// human input may create it. Agent/system comments remain prompt context but
	// cannot expand another run's Skill set.
	if task.TriggerCommentID.Valid && resp.TriggerAuthorType == "member" {
		markdowns = append(markdowns, resp.TriggerCommentContent)
	}
	if len(task.CoalescedCommentIds) > 0 {
		for _, comment := range resp.CoalescedComments {
			if comment.AuthorType == "member" {
				markdowns = append(markdowns, comment.Content)
			}
		}
	}
	return selectedSlashSkillIDs(markdowns...)
}

func mergeSelectedSkillIDs(groups ...[]string) []string {
	ids := make([]string, 0, maxSelectedSlashSkillsPerTask)
	seen := make(map[string]struct{}, maxSelectedSlashSkillsPerTask)
	for _, group := range groups {
		for _, id := range group {
			if _, ok := seen[id]; ok {
				continue
			}
			seen[id] = struct{}{}
			ids = append(ids, id)
			if len(ids) == maxSelectedSlashSkillsPerTask {
				return ids
			}
		}
	}
	return ids
}

func mergeTaskSkills(
	configured []service.AgentSkillData,
	selected []service.AgentSkillData,
) []service.AgentSkillData {
	merged := make([]service.AgentSkillData, 0, len(configured)+len(selected))
	seen := make(map[string]struct{}, len(configured)+len(selected))
	add := func(skill service.AgentSkillData) {
		key := skill.Source + "\x00" + skill.ID
		if _, ok := seen[key]; ok {
			return
		}
		seen[key] = struct{}{}
		merged = append(merged, skill)
	}
	for _, skill := range configured {
		add(skill)
	}
	for _, skill := range selected {
		add(skill)
	}
	return merged
}

func selectedSkillUUIDs(skills []service.AgentSkillData) []pgtype.UUID {
	ids := make([]pgtype.UUID, 0, len(skills))
	for _, skill := range skills {
		id, err := util.ParseUUID(skill.ID)
		if err == nil {
			ids = append(ids, id)
		}
	}
	return ids
}

// applyClaimTaskSkills augments the already-loaded claim Skills with the
// same-workspace Skills selected in this exact payload. Reusing that payload
// avoids a second full load and keeps upstream's fail-closed read contract.
// Only validated selected UUIDs are retained for transactional claim
// finalization and later bundle-resolution authorization.
func (h *Handler) applyClaimTaskSkills(
	ctx context.Context,
	task db.AgentTaskQueue,
	resp *AgentTaskResponse,
	useSkillRefs bool,
	configuredSkillCount int,
) (agentSkillCount int, builtinSkillCount int, failure *claimBuildFailure) {
	if resp.Agent == nil {
		return 0, 0, nil
	}

	workspaceID, err := util.ParseUUID(resp.WorkspaceID)
	if err != nil {
		return 0, 0, &claimBuildFailure{
			outcome: "error_selected_skills",
			status:  http.StatusInternalServerError,
			message: "failed to resolve task workspace Skills",
		}
	}
	storedSelectedIDs, err := selectedSkillIDsFromTaskContext(task.Context)
	if err != nil {
		return 0, 0, &claimBuildFailure{
			outcome: "error_selected_skills",
			status:  http.StatusInternalServerError,
			message: "failed to resolve task workspace Skills",
		}
	}
	selectedIDs := mergeSelectedSkillIDs(
		storedSelectedIDs,
		selectedSlashSkillIDsForClaim(task, *resp),
	)
	selected, err := h.TaskService.LoadWorkspaceSkillsByIDs(
		ctx,
		workspaceID,
		selectedIDs,
	)
	if err != nil {
		return 0, 0, &claimBuildFailure{
			outcome: "error_selected_skills",
			status:  http.StatusInternalServerError,
			message: "failed to load selected workspace Skills",
		}
	}

	resp.selectedSkillIDs = selectedSkillUUIDs(selected)
	if useSkillRefs {
		_, selectedRefs := service.BuildAgentSkillBundles(selected)
		refs := make([]service.AgentSkillRefData, 0, len(resp.Agent.SkillRefs)+len(selectedRefs))
		builtins := make([]service.AgentSkillRefData, 0)
		seen := make(map[string]struct{}, len(resp.Agent.SkillRefs))
		for _, ref := range resp.Agent.SkillRefs {
			seen[service.AgentSkillBundleKey(ref.Source, ref.ID)] = struct{}{}
			if ref.Source == skillbundle.SourceBuiltin {
				builtins = append(builtins, ref)
			} else {
				refs = append(refs, ref)
			}
		}
		for _, ref := range selectedRefs {
			key := service.AgentSkillBundleKey(ref.Source, ref.ID)
			if _, exists := seen[key]; !exists {
				refs = append(refs, ref)
				seen[key] = struct{}{}
			}
		}
		refs = append(refs, builtins...)
		resp.Agent.SkillRefs = refs
		return len(refs), 0, nil
	}

	// The claim loader puts configured Skills before built-ins. Keep selected
	// workspace Skills between those groups, as in the original custom payload.
	configured := resp.Agent.Skills[:configuredSkillCount]
	builtins := resp.Agent.Skills[configuredSkillCount:]
	workspaceSkills := mergeTaskSkills(configured, selected)
	resp.Agent.Skills = append(workspaceSkills, builtins...)
	return len(workspaceSkills), len(builtins), nil
}

type persistedSelectedSkillContext struct {
	SelectedSkillIDs []string `json:"selected_skill_ids"`
}

func selectedSkillIDsFromTaskContext(raw []byte) ([]string, error) {
	if len(raw) == 0 {
		return nil, nil
	}
	var stored persistedSelectedSkillContext
	if err := json.Unmarshal(raw, &stored); err != nil {
		// Older/custom tasks may carry a non-object context. That shape cannot
		// contain a server-owned selected-Skill grant, so attached/built-in
		// bundle resolution must retain its pre-feature behavior.
		var typeErr *json.UnmarshalTypeError
		if errors.As(err, &typeErr) {
			return nil, nil
		}
		return nil, fmt.Errorf("decode selected skill grant: %w", err)
	}
	if len(stored.SelectedSkillIDs) > maxSelectedSlashSkillsPerTask {
		stored.SelectedSkillIDs = stored.SelectedSkillIDs[:maxSelectedSlashSkillsPerTask]
	}
	return stored.SelectedSkillIDs, nil
}

func (h *Handler) selectedTaskSkillBundlesForResolve(
	ctx context.Context,
	task db.AgentTaskQueue,
	workspaceID pgtype.UUID,
	wanted []service.AgentSkillBundleRef,
) ([]service.AgentSkillData, error) {
	selectedIDs, err := selectedSkillIDsFromTaskContext(task.Context)
	if err != nil {
		return nil, err
	}
	granted := make(map[string]struct{}, len(selectedIDs))
	for _, id := range selectedIDs {
		granted[id] = struct{}{}
	}
	requestedIDs := make([]string, 0, len(selectedIDs))
	for _, ref := range wanted {
		if ref.Source == skillbundle.SourceWorkspace {
			if _, ok := granted[ref.ID]; ok {
				requestedIDs = append(requestedIDs, ref.ID)
				delete(granted, ref.ID)
			}
		}
	}
	selected, err := h.TaskService.LoadWorkspaceSkillsByIDs(ctx, workspaceID, requestedIDs)
	if err != nil {
		return nil, err
	}
	bundles, _ := service.BuildAgentSkillBundles(selected)
	return bundles, nil
}
