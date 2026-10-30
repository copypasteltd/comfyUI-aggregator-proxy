import { useEffect, useRef, useState } from "react";
import { Alert, Button } from "antd";
import { RotateCcw } from "lucide-react";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { embeddedGlbUri } from "@/lib/studio-glb";
import { lime } from "@/lib/lime-theme";

export default function StudioModel3dViewer({ data }: { data: ArrayBuffer }) {
    const host = useRef<HTMLDivElement>(null), reset = useRef<() => void>(() => {});
    const [error, setError] = useState(""), [stats, setStats] = useState("");
    useEffect(() => {
        const element = host.current!;
        let disposed = false, renderer: THREE.WebGLRenderer | undefined, controls: OrbitControls | undefined, observer: ResizeObserver | undefined;
        const geometries = new Set<THREE.BufferGeometry>(), materials = new Set<THREE.Material>(), textures = new Set<THREE.Texture>(), skeletons = new Set<THREE.Skeleton>(), urls = new Set<string>(), closedImages = new Set<ImageBitmap>();
        const closeTexture = (texture: THREE.Texture) => {
            const image = texture.source?.data;
            for (const item of Array.isArray(image) ? image : [image]) if (typeof ImageBitmap !== "undefined" && item instanceof ImageBitmap && !closedImages.has(item)) { item.close(); closedImages.add(item); }
            texture.dispose();
        };
        const trackTexture = (texture: THREE.Texture) => { if (!textures.has(texture)) { textures.add(texture); if (disposed) closeTexture(texture); } };
        const trackMaterial = (material: THREE.Material) => {
            if (materials.has(material)) return;
            materials.add(material);
            for (const value of Object.values(material)) if (value?.isTexture) trackTexture(value);
            if (disposed) material.dispose();
        };
        const trackObject = (root: THREE.Object3D) => root.traverse((object) => {
            const mesh = object as THREE.Mesh & THREE.SkinnedMesh;
            if (mesh.geometry && !geometries.has(mesh.geometry)) { geometries.add(mesh.geometry); if (disposed) mesh.geometry.dispose(); }
            for (const material of mesh.material ? Array.isArray(mesh.material) ? mesh.material : [mesh.material] : []) trackMaterial(material);
            if (mesh.skeleton && !skeletons.has(mesh.skeleton)) { skeletons.add(mesh.skeleton); if (disposed) mesh.skeleton.dispose(); }
        });
        const cleanup = () => {
            if (disposed) return; disposed = true; reset.current = () => {};
            observer?.disconnect(); controls?.dispose();
            if (renderer) { renderer.domElement.removeEventListener("webglcontextlost", contextLost); renderer.dispose(); renderer.forceContextLoss(); renderer.domElement.remove(); }
            for (const geometry of geometries) geometry.dispose();
            for (const material of materials) material.dispose();
            for (const texture of textures) closeTexture(texture);
            for (const skeleton of skeletons) skeleton.dispose();
            for (const url of urls) URL.revokeObjectURL(url);
        };
        const fail = (reason: unknown) => { if (!disposed) { cleanup(); setError(reason instanceof Error ? reason.message : "当前浏览器无法展示该 GLB，请保存原件打开"); } };
        const contextLost = (event: Event) => { event.preventDefault(); fail(new Error("浏览器三维上下文已释放；可关闭后重新打开，或保存原件")); };
        const manager = new THREE.LoadingManager();
        manager.onError = () => fail(new Error("GLB 内嵌贴图无法读取，请保存原件检查"));
        manager.setURLModifier((url) => {
            if (url.startsWith(`blob:${location.origin}/`)) { urls.add(url); if (disposed) URL.revokeObjectURL(url); return url; }
            if (embeddedGlbUri(url)) return url;
            throw new Error("已阻止三维资源外链，请保存 GLB 原件打开");
        });
        const loader = new GLTFLoader(manager);
        // Track dependency completion too: a closed or failed parse may finish textures later.
        loader.register((parser) => ({ name: "studio-resource-lifecycle", beforeRoot: () => {
            const get = parser.getDependency.bind(parser);
            parser.getDependency = (type, index) => get(type, index).then((value) => {
                if (type === "texture" && value) trackTexture(value);
                if (type === "material" && value) trackMaterial(value);
                if ((type === "mesh" || type === "scene") && value) trackObject(value);
                return value;
            });
            return null;
        } }));
        setError(""); setStats("");
        void loader.parseAsync(data, "").then((gltf) => {
            for (const scene of gltf.scenes) trackObject(scene);
            if (disposed) return;
            const bounds = new THREE.Box3().setFromObject(gltf.scene), sphere = bounds.getBoundingSphere(new THREE.Sphere());
            if (bounds.isEmpty() || !Number.isFinite(sphere.radius) || sphere.radius <= 0) throw new Error("GLB 没有可展示的几何体，请保存原件检查");
            renderer = new THREE.WebGLRenderer({ antialias: true });
            renderer.domElement.setAttribute("aria-label", "三维模型交互区"); renderer.domElement.setAttribute("role", "img"); renderer.domElement.tabIndex = 0;
            renderer.domElement.style.cssText = "display:block;width:100%;height:100%;outline-offset:-3px";
            renderer.domElement.addEventListener("webglcontextlost", contextLost); element.appendChild(renderer.domElement);
            const scene = new THREE.Scene(); scene.background = new THREE.Color(lime.canvas); scene.add(gltf.scene, new THREE.HemisphereLight(lime.card, lime.muted, 3));
            const light = new THREE.DirectionalLight(lime.card, 3); light.position.set(1, 2, 3); scene.add(light);
            const camera = new THREE.PerspectiveCamera(40, 1, sphere.radius / 1000, sphere.radius * 1000);
            controls = new OrbitControls(camera, renderer.domElement); controls.target.copy(sphere.center); controls.listenToKeyEvents(renderer.domElement);
            const render = () => { try { if (!disposed && renderer) renderer.render(scene, camera); } catch (reason) { fail(reason); } };
            const fit = () => {
                const fov = 2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * Math.min(1, camera.aspect));
                camera.position.copy(sphere.center).add(new THREE.Vector3(1, 0.6, 1).normalize().multiplyScalar(sphere.radius / Math.sin(fov / 2) * 1.15));
                controls!.target.copy(sphere.center); controls!.update(); render();
            };
            reset.current = fit;
            controls.addEventListener("change", render);
            let framed = false;
            observer = new ResizeObserver(() => {
                if (disposed || !renderer || !element.clientWidth || !element.clientHeight) return;
                renderer.setSize(element.clientWidth, element.clientHeight, false); camera.aspect = element.clientWidth / element.clientHeight; camera.updateProjectionMatrix();
                if (!framed) { framed = true; fit(); } else render();
            }); observer.observe(element);
            let vertices = 0, triangles = 0;
            gltf.scene.traverse((object) => { const mesh = object as THREE.Mesh & THREE.InstancedMesh; if (mesh.isMesh) { const instances = mesh.isInstancedMesh ? mesh.count : 1, count = mesh.geometry.attributes.position?.count || 0; vertices += count * instances; triangles += (mesh.geometry.index?.count || count) / 3 * instances; } });
            setStats(`${vertices.toLocaleString()} 顶点 · ${Math.floor(triangles).toLocaleString()} 三角面`);
        }).catch(fail);
        return cleanup;
    }, [data]);
    return <>
        {error && <Alert type="warning" showIcon title="暂时无法交互预览" description={error} />}
        <div ref={host} role="group" aria-label="三维视图容器" style={{ width: "100%", height: "min(52vh, 520px)", minHeight: 240, borderRadius: 12, overflow: "hidden", display: error ? "none" : "block", background: "var(--cv)" }} />
        {!error && <div className="studio-actions" style={{ flexWrap: "wrap" }}><Button icon={<RotateCcw size={14} />} disabled={!stats} onClick={() => reset.current()}>复位视图</Button><span role="status">{stats || "正在解析三维原件…"}</span></div>}
        <p className="studio-note" style={{ marginTop: 12 }}>聚焦模型后可用方向键平移，Ctrl + 方向键旋转。当前展示静态几何与材质；动画、压缩解码和未支持的扩展可另存后查看。</p>
    </>;
}
