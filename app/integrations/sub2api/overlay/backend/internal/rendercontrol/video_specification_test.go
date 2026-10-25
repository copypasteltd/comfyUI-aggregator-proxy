package rendercontrol

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

func TestVideoSpecificationPreservesExactMillisecondsAndSafeTerminalErrors(t *testing.T) {
	for _, item := range []struct {
		seconds string
		millis  int64
		output  string
	}{{"0.001", 1, "0.001"}, {"0.010", 10, "0.01"}, {"0.1", 100, "0.1"}, {"5.125", 5125, "5.125"}, {"5.000", 5000, "5"}, {"86400", 86400000, "86400"}} {
		t.Run(item.seconds, func(t *testing.T) {
			request := controlclient.JobRequest{Model: "studio-video-v1", TaskType: "text_to_video", Input: map[string]any{"prompt": "private synthetic prompt"}, Parameters: map[string]any{"seconds": json.Number(item.seconds), "size": "1024x1024"}}
			frozen, err := NewRequest("videos.create", request)
			if err != nil || frozen.videoMillis != item.millis || frozen.videoSize != "1024x1024" {
				t.Fatal("exact video specification was not captured")
			}
			view := compatibilityView(localPublicJob(Intent{TaskType: "text_to_video", RequestedVideoMilliseconds: frozen.videoMillis, VideoSize: frozen.videoSize}), true)
			if view["seconds"] != item.output || view["size"] != "1024x1024" || view["progress"] != nil {
				t.Fatal("video projection guessed or rounded its specification")
			}
		})
	}
	for status, code := range map[string]string{"failed": "generation_failed", "cancelled": "task_cancelled", "expired": "task_expired", "running": "", "succeeded": ""} {
		t.Run(status, func(t *testing.T) {
			view := compatibilityView(publicJob{Status: status}, true)
			encoded, _ := json.Marshal(view)
			var value struct {
				Error *struct {
					Code    string `json:"code"`
					Message string `json:"message"`
				} `json:"error"`
			}
			if json.Unmarshal(encoded, &value) != nil || code != "" && (value.Error == nil || value.Error.Code != code || value.Error.Message == "") || code == "" && value.Error != nil {
				t.Fatal("video terminal error did not use the safe status mapping")
			}
			if view["seconds"] != nil || view["size"] != nil || view["progress"] != nil {
				t.Fatal("legacy unknown metadata was invented")
			}
		})
	}
	image := compatibilityView(publicJob{Status: "failed", VideoMilliseconds: 5125, VideoSize: "1024x1024"}, false)
	if image["seconds"] != nil || image["size"] != nil || image["error"] != nil {
		t.Fatal("video projection changed the image compatibility object")
	}
}

func TestVideoReadRetainsFrozenMetadataWhenRequestCipherUnavailable(t *testing.T) {
	f := compatibilitySetup(t)
	f.config.Models = append(f.config.Models, PublicModelRule{Model: "studio-video-v1", TaskType: "text_to_video", Target: testTarget(t), PriceVersion: "prices-v1", UnitPrice: money(t, "1"), GroupIDs: []int64{f.groupID}, Defaults: map[string]any{"seconds": json.Number("5.125"), "size": "1280x720"}})
	f.start(t)
	status, raw, _ := f.call(t, "POST", "/v1/videos", `{"model":"studio-video-v1","prompt":"private synthetic prompt"}`, map[string]string{"Idempotency-Key": "video-cipher-unavailable"})
	if status != 202 {
		t.Fatal("video fixture was not accepted")
	}
	id := resultID(t, raw)
	runSQL(t, `UPDATE render_control_intents SET request_cipher=decode(repeat('00',30),'hex') WHERE id=$1`, id)
	status, raw, _ = f.call(t, "GET", "/v1/videos/"+id, "", nil)
	var value map[string]any
	if status != 200 || json.Unmarshal(raw, &value) != nil || value["seconds"] != "5.125" || value["size"] != "1280x720" {
		t.Fatal("customer video metadata read attempted to recover the encrypted request")
	}
}

func TestVideoSpecificationMigrationPreservesLegacyUnknownAndRejectsMutation(t *testing.T) {
	// Exercise the exact forward migration over existing rows in a disposable
	// schema. Rollback removes the schema/functions without touching public data.
	tx, err := begin(ctx(), testDB)
	if err != nil {
		t.Fatal("migration fixture transaction failed")
	}
	defer tx.Rollback()
	name, err := newID("test")
	if err != nil {
		t.Fatal("migration fixture identity failed")
	}
	if _, err = tx.Exec(`CREATE SCHEMA ` + name); err != nil {
		t.Fatal("migration fixture schema failed")
	}
	if _, err = tx.Exec(`SET LOCAL search_path TO ` + name); err != nil {
		t.Fatal("migration fixture search path failed")
	}
	if _, err = tx.Exec(`CREATE TABLE render_control_intents(id TEXT PRIMARY KEY, task_type TEXT NOT NULL); INSERT INTO render_control_intents VALUES ('legacy-video','text_to_video'),('legacy-image','text_to_image'),('legacy-audio','text_to_audio')`); err != nil {
		t.Fatal("legacy migration fixtures failed")
	}
	migration, err := os.ReadFile(filepath.Join("migrations", "258_render_control_video_specification.sql"))
	if err != nil {
		migration, err = os.ReadFile(filepath.Join("..", "..", "migrations", "258_render_control_video_specification.sql"))
	}
	if err != nil {
		t.Fatal("reviewed video migration was unavailable")
	}
	if _, err = tx.Exec(string(migration)); err != nil {
		t.Fatal("forward video migration failed")
	}
	var unknown int
	if tx.QueryRow(`SELECT count(*) FROM render_control_intents WHERE requested_video_milliseconds IS NULL AND video_size IS NULL`).Scan(&unknown) != nil || unknown != 3 {
		t.Fatal("migration invented specifications for existing ciphertext")
	}
	if _, err = tx.Exec(`INSERT INTO render_control_intents VALUES ('new-video','text_to_video',5125,'1280x720'),('new-video-no-size','references_to_video',1,NULL)`); err != nil {
		t.Fatal("valid frozen video metadata rejected")
	}
	for _, query := range []string{
		`UPDATE render_control_intents SET video_size='512x512' WHERE id='new-video'`,
		`UPDATE render_control_intents SET requested_video_milliseconds=6000 WHERE id='new-video'`,
		`UPDATE render_control_intents SET requested_video_milliseconds=1000 WHERE id='legacy-video'`,
		`INSERT INTO render_control_intents VALUES ('bad-image','text_to_image',1000,NULL)`,
		`INSERT INTO render_control_intents VALUES ('bad-audio','text_to_audio',1000,NULL)`,
		`INSERT INTO render_control_intents VALUES ('bad-zero','text_to_video',0,NULL)`,
		`INSERT INTO render_control_intents VALUES ('bad-long','text_to_video',86400001,NULL)`,
		`INSERT INTO render_control_intents VALUES ('bad-size','text_to_video',1000,'private')`,
		`INSERT INTO render_control_intents VALUES ('bad-unknown','text_to_video',NULL,'1280x720')`,
	} {
		if _, err = tx.Exec(`SAVEPOINT video_metadata_case`); err != nil {
			t.Fatal("migration fixture savepoint failed")
		}
		_, rejected := tx.Exec(query)
		if _, err = tx.Exec(`ROLLBACK TO SAVEPOINT video_metadata_case`); err != nil {
			t.Fatal("migration fixture rollback failed")
		}
		if rejected == nil {
			t.Fatal("database accepted a mutable or invalid video specification")
		}
	}
}
