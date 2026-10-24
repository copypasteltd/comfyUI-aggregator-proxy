package controlclient

import (
	"encoding/json"
	"strings"
	"testing"
)

func audioCapability() PublishedCapability {
	m := catalogSample()
	duration := 2.001
	m.TaskType = "text_to_audio"
	m.SupportedParameters = []string{"seconds", "quality", "seed", "negative_prompt"}
	m.Presets = []PublicPreset{{Quality: "standard", Seconds: &duration, AudioFormat: "wav", SampleRate: 24000, Channels: 1}}
	m.Output.MediaTypes = []string{"audio/wav"}
	return m
}

func TestAudioCatalogPreservesActualFormatWithoutImageSize(t *testing.T) {
	for format, media := range map[string]string{"wav": "audio/wav", "flac": "audio/flac", "mp3": "audio/mpeg", "opus": "audio/ogg"} {
		m := audioCapability()
		m.Presets[0].AudioFormat, m.Output.MediaTypes = format, []string{media}
		if format == "opus" {
			m.Presets[0].SampleRate = 48000
		}
		if !validCapability(m) {
			t.Fatal("valid audio specification rejected", format)
		}
		raw, err := json.Marshal(m)
		if err != nil || strings.Contains(string(raw), `"size"`) || !strings.Contains(string(raw), `"sample_rate"`) {
			t.Fatal("audio discovery projection lost its format")
		}
	}
	for name, change := range map[string]func(*PublishedCapability){
		"size":                   func(m *PublishedCapability) { m.Presets[0].Size = "1024x1024" },
		"image_parameter":        func(m *PublishedCapability) { m.SupportedParameters = []string{"size"} },
		"fps_parameter":          func(m *PublishedCapability) { m.SupportedParameters = []string{"fps"} },
		"duration_missing":       func(m *PublishedCapability) { m.Presets[0].Seconds = nil },
		"too_long":               func(m *PublishedCapability) { n := 600.001; m.Presets[0].Seconds = &n },
		"fractional_millisecond": func(m *PublishedCapability) { n := 1.0001; m.Presets[0].Seconds = &n },
		"rate":                   func(m *PublishedCapability) { m.Presets[0].SampleRate = 7999 },
		"channels":               func(m *PublishedCapability) { m.Presets[0].Channels = 0 },
		"opus_rate": func(m *PublishedCapability) {
			m.Presets[0].AudioFormat = "opus"
			m.Output.MediaTypes = []string{"audio/ogg"}
		},
		"codec":       func(m *PublishedCapability) { m.Presets[0].AudioFormat = "aac" },
		"mime":        func(m *PublishedCapability) { m.Output.MediaTypes = []string{"video/mp4"} },
		"format_mime": func(m *PublishedCapability) { m.Output.MediaTypes = []string{"audio/flac"} },
		"bytes":       func(m *PublishedCapability) { m.Output.MaxBytes = 256<<20 + 1 },
	} {
		t.Run(name, func(t *testing.T) {
			m := audioCapability()
			change(&m)
			if validCapability(m) {
				t.Fatal("invalid audio specification accepted")
			}
		})
	}
}

func TestAudioDoesNotChangeLegacySettlementEncodingOrMediaFamily(t *testing.T) {
	raw, _ := json.Marshal(Settlement{ImageCount: 1})
	if strings.Contains(string(raw), "audio_milliseconds") {
		t.Fatal("legacy terminal fingerprint changed")
	}
	raw, _ = json.Marshal(Settlement{AudioMilliseconds: 500})
	if !strings.Contains(string(raw), `"audio_milliseconds":500`) {
		t.Fatal("audio usage omitted")
	}
	for _, media := range []string{"audio/wav", "audio/flac", "audio/mpeg", "audio/ogg"} {
		if !allowedMedia(media) || !taskMediaMatches("text_to_audio", media) || taskMediaMatches("text_to_image", media) || taskMediaMatches("text_to_video", media) {
			t.Fatal("media family drift", media)
		}
	}
}
