import { useEffect, useState } from "react";
import { getImageBlob } from "@/services/image-storage";

export default function StudioReferencePreview({ storageKey, blob, name, identity }: { storageKey?: string; blob?: Blob; name: string; identity: string }) {
    const [url, setUrl] = useState<string>();
    const [missing, setMissing] = useState(false);
    useEffect(() => {
        let active = true;
        let objectUrl: string | undefined;
        setUrl(undefined); setMissing(false);
        void (async () => {
            try {
                const original = blob || (storageKey ? await getImageBlob(storageKey) : null);
                if (!active) return;
                if (!original) { setMissing(true); return; }
                objectUrl = URL.createObjectURL(original); setUrl(objectUrl);
            } catch { if (active) setMissing(true); }
        })();
        return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
    }, [storageKey, blob, identity]);
    return <div className="studio-actions" role="status" style={{ alignItems: "center" }}>
        {url && <img src={url} alt={name} style={{ width: 76, height: 76, objectFit: "contain", borderRadius: 8 }} />}
        <span>{name}{missing ? " · 本机原件不可读取，请重新选择" : url ? " · 已选用" : " · 正在读取预览"}</span>
    </div>;
}
