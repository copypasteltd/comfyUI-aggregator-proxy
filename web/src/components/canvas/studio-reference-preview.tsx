import { useEffect, useRef, useState } from "react";
import { getImageBlob } from "@/services/image-storage";
import { getMediaBlob } from "@/services/file-storage";
import type { AssetKind } from "@/services/api/aggregator/types";

export default function StudioReferencePreview({ storageKey, blob, name, identity, kind = "image" }: { storageKey?: string; blob?: Blob; name: string; identity: string; kind?: AssetKind }) {
    const [url, setUrl] = useState<string>();
    const [missing, setMissing] = useState(false);
    const media = useRef<HTMLMediaElement | null>(null);
    useEffect(() => {
        let active = true;
        let objectUrl: string | undefined;
        setUrl(undefined); setMissing(false);
        void (async () => {
            try {
                const original = blob || (storageKey ? await (kind === "image" ? getImageBlob(storageKey) : getMediaBlob(storageKey)) : null);
                if (!active) return;
                if (!original) { setMissing(true); return; }
                objectUrl = URL.createObjectURL(original); setUrl(objectUrl);
            } catch { if (active) setMissing(true); }
        })();
        return () => { active = false; if (media.current) { media.current.pause(); media.current.removeAttribute("src"); media.current.load(); } if (objectUrl) URL.revokeObjectURL(objectUrl); };
    }, [storageKey, blob, identity, kind]);
    return <div className="studio-actions" role="status" style={{ alignItems: "center" }}>
        {url && kind === "image" && <img src={url} alt={name} style={{ width: 76, height: 76, objectFit: "contain", borderRadius: 8 }} />}
        {url && kind === "video" && <video ref={(element) => { media.current = element; }} aria-label={name} src={url} controls preload="metadata" style={{ width: "100%", maxWidth: 240 }} />}
        {url && kind === "audio" && <audio ref={(element) => { media.current = element; }} aria-label={name} src={url} controls preload="metadata" style={{ width: "100%", maxWidth: 280 }} />}
        <span>{name}{missing ? " · 本机原件不可读取，请重新选择" : url ? " · 已选用" : " · 正在读取预览"}</span>
    </div>;
}
