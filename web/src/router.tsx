import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate, Outlet } from "react-router-dom";
import { MANAGED_STUDIO } from "@/constant/studio";
import StudioLayout from "@/layouts/studio-layout";

const UserLayout = lazy(() => import("@/layouts/user-layout"));
const CanvasPage = lazy(() => import("@/pages/canvas"));
const CanvasProjectPage = lazy(() => import("@/pages/canvas/project"));
const AssetsPage = lazy(() => import("@/pages/assets"));
const ConfigPage = lazy(() => import("@/pages/config"));
const HomePage = lazy(() => import("@/pages/home"));
const ImagePage = lazy(() => import("@/pages/image"));
const PromptsPage = lazy(() => import("@/pages/prompts"));
const VideoPage = lazy(() => import("@/pages/video"));
const NotFound = lazy(() => import("@/pages/not-found"));
const ConnectionPage = lazy(() => import("@/pages/studio/connection"));
const ModelsPage = lazy(() => import("@/pages/studio/models"));
const TasksPage = lazy(() => import("@/pages/studio/tasks"));
const StudioAssetsPage = lazy(() => import("@/pages/studio/assets"));

function RoutesLayout() {
    const content = <Suspense fallback={<div className="studio-route-loading" role="status">正在打开画布…</div>}><Outlet /></Suspense>;
    return MANAGED_STUDIO ? <StudioLayout>{content}</StudioLayout> : <Suspense fallback={null}><UserLayout>{content}</UserLayout></Suspense>;
}

export const router = createBrowserRouter([
    { element: <RoutesLayout />, children: [
        { path: "/", element: MANAGED_STUDIO ? <Navigate to="/canvas" replace /> : <HomePage /> },
        { path: "/canvas", element: <CanvasPage /> },
        { path: "/canvas/:id", element: <CanvasProjectPage /> },
        { path: "/config", element: MANAGED_STUDIO ? <ConnectionPage /> : <ConfigPage /> },
        { path: "/models", element: <ModelsPage /> },
        { path: "/tasks", element: <TasksPage /> },
        { path: "/image", element: MANAGED_STUDIO ? <Navigate to="/models" replace /> : <ImagePage /> },
        { path: "/video", element: MANAGED_STUDIO ? <Navigate to="/models" replace /> : <VideoPage /> },
        { path: "/assets", element: MANAGED_STUDIO ? <StudioAssetsPage /> : <AssetsPage /> },
        { path: "/prompts", element: MANAGED_STUDIO ? <Navigate to="/models" replace /> : <PromptsPage /> },
        { path: "*", element: <NotFound /> },
    ] },
], { basename: import.meta.env.BASE_URL.replace(/\/$/, "") || "/" });
