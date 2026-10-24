package rendercontrol

import (
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"testing"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

func audioRequest(t *testing.T, duration string) Request {
	t.Helper()
	r, err := NewRequest("audio.create", controlclient.JobRequest{Model: "studio-audio-v1", TaskType: "text_to_audio", Input: map[string]any{"prompt": "Synthetic audio Workflow"}, Parameters: map[string]any{"seconds": json.Number(duration), "quality": "standard"}})
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func TestAudioPublicRequestNeedsNoImageSizeOrImageGroupPermission(t *testing.T) {
	f := publicSetup(t)
	f.config.Models[0].TaskType = "text_to_audio"
	f.config.Models[0].Defaults = map[string]any{"seconds": json.Number("2.001"), "quality": "standard"}
	runSQL(t, `UPDATE groups SET allow_image_generation=FALSE WHERE id=$1`, f.groupID)
	f.start(t)
	status, raw, _ := f.call(t, "POST", "/v1/jobs", `{"model":"studio-image-v1","task_type":"text_to_audio","input":{"prompt":"Synthetic Workflow audio"}}`, map[string]string{"Idempotency-Key": "audio-native-http"})
	var job publicJob
	if status != 202 || json.Unmarshal(raw, &job) != nil || job.TaskType != "text_to_audio" {
		t.Fatal("audio-only public task rejected", status)
	}
	i, request, err := f.s.Recover(ctx(), job.ID)
	if err != nil || i.Operation != "audio.create" || i.MaxUnits != 3 || request.Parameters["seconds"] != json.Number("2.001") {
		t.Fatal("audio default or reservation not frozen", err)
	}
	if _, exists := request.Parameters["size"]; exists {
		t.Fatal("image size injected into audio request")
	}
	balances(t, f.subject, "7.00000000", "3.00000000", "0.00000000")
}

func TestAudioSettlementUsesIndependentActualMeterAndExactlyOneNativeUsage(t *testing.T) {
	f := publicSetup(t)
	request := audioRequest(t, "5.125")
	request.usageAccountID = f.config.Models[0].UsageAccountID
	// Independent image/video multipliers must not be applied to audio.
	runSQL(t, `UPDATE groups SET image_rate_independent=TRUE,image_rate_multiplier=9,video_rate_independent=TRUE,video_rate_multiplier=8 WHERE id=$1`, f.groupID)
	i, err := f.s.Reserve(ctx(), f.subject, testTarget(t), "audio-meter", request, Quote{"audio-v1", money(t, "1"), 6})
	if err != nil || i.RateSource != "group" || i.RateMultiplier.String() != "1.0000" {
		t.Fatal("audio pricing used another media multiplier", err, i.RateSource)
	}
	if _, err = f.s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	e := event(t, i, "succeeded", 0)
	e.AudioMilliseconds = 1001
	var wg sync.WaitGroup
	results := make(chan error, 12)
	for range 12 {
		wg.Go(func() { _, err := f.s.Finalize(ctx(), testTarget(t), i.Instance, e); results <- err })
	}
	wg.Wait()
	close(results)
	for err := range results {
		if err != nil {
			t.Fatal("audio replay settlement", err)
		}
	}
	settled, err := f.s.Load(ctx(), f.subject, i.ID)
	if err != nil || settled.ActualAmount.String() != "2.00000000" || settled.ImageCount != 0 || settled.VideoMilliseconds != 0 || settled.AudioMilliseconds != 1001 {
		t.Fatal("audio terminal meter mixed with image/video", err)
	}
	balances(t, f.subject, "8.00000000", "0.00000000", "2.00000000")
	runSQL(t, `UPDATE render_control_effects SET acknowledged_at=NOW() WHERE intent_id=$1 AND kind='reserved'`, i.ID)
	c := effectConsumer(t, f.s, &effectCacheFixture{}, i.Instance)
	if worked, err := c.Step(ctx()); !worked || err != nil {
		t.Fatal("audio first usage pass", err)
	}
	dueEffects(t, i.ID)
	if worked, err := c.Step(ctx()); !worked || err != nil {
		t.Fatal("audio replay usage pass", err)
	}
	if countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1 AND image_count=0 AND video_count=0 AND video_duration_seconds IS NULL AND audio_count=1 AND audio_duration_milliseconds=1001 AND billing_mode='audio' AND actual_cost=2 AND inbound_endpoint='/v1/jobs'`, settled.LedgerRef) != 1 {
		t.Fatal("audio native usage was not recorded exactly once")
	}
	if countRows(t, `SELECT count(*) FROM usage_billing_dedup WHERE request_id=$1`, "render-control:"+i.ID+":terminal") != 1 {
		t.Fatal("audio terminal billing duplicated")
	}
}

func TestAudioSettlementRejectsWrongMetersAndRefundsTerminalFailures(t *testing.T) {
	for _, outcome := range []string{"succeeded", "failed", "cancelled", "expired"} {
		t.Run(outcome, func(t *testing.T) {
			s, subject := store(t), fixture(t, "10")
			r := audioRequest(t, "2.001")
			i, err := s.Reserve(ctx(), subject, testTarget(t), "audio-failure-"+outcome, r, Quote{"audio-v1", money(t, "1"), 3})
			if err != nil {
				t.Fatal(err)
			}
			if _, err = s.MarkDispatch(ctx(), i.ID); err != nil {
				t.Fatal(err)
			}
			e := event(t, i, outcome, 0)
			if outcome == "succeeded" {
				e.AudioMilliseconds = 500
			}
			bad := e
			bad.ImageCount = 1
			if _, err = s.Finalize(ctx(), testTarget(t), i.Instance, bad); !errors.Is(err, ErrConflict) {
				t.Fatal("audio accepted image charge", err)
			}
			bad = e
			bad.VideoMilliseconds = 1
			if _, err = s.Finalize(ctx(), testTarget(t), i.Instance, bad); !errors.Is(err, ErrConflict) {
				t.Fatal("audio accepted video charge", err)
			}
			if outcome != "succeeded" {
				bad = e
				bad.AudioMilliseconds = 1
				if _, err = s.Finalize(ctx(), testTarget(t), i.Instance, bad); !errors.Is(err, ErrConflict) {
					t.Fatal("failed task charged audio", err)
				}
			}
			if _, err = s.Finalize(ctx(), testTarget(t), i.Instance, e); err != nil {
				t.Fatal(err)
			}
			if outcome == "succeeded" {
				balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
			} else {
				balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
			}
		})
	}
}

func TestAudioRequestOperationAndDurationBounds(t *testing.T) {
	r := audioRequest(t, "600")
	if r.units != 600 {
		t.Fatal("audio units not in started seconds")
	}
	for _, duration := range []string{"0", "600.001", "1.0001", "NaN"} {
		raw := []byte(`{"model":"a","task_type":"text_to_audio","input":{"prompt":"synthetic"},"parameters":{"seconds":` + duration + `}}`)
		if _, err := ParsePublicRequest(raw); !errors.Is(err, ErrInvalid) {
			t.Fatal("audio duration accepted", duration)
		}
	}
	if _, err := NewRequest("videos.create", controlclient.JobRequest{Model: "a", TaskType: "text_to_audio"}); !errors.Is(err, ErrInvalid) {
		t.Fatal("audio accepted video operation")
	}
	encoded, _ := json.Marshal(controlclient.Settlement{ImageCount: 1})
	if strings.Contains(string(encoded), "audio_milliseconds") {
		t.Fatal("old terminal hash changed")
	}
}
