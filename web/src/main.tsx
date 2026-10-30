import React from "react";
import { createRoot } from "react-dom/client";
import "antd/dist/reset.css";
import "streamdown/styles.css";
import "./styles/globals.css";
import "./styles/studio.css";
import { RouterProvider } from "react-router-dom";

import { AppProviders } from "@/components/layout/app-providers";
import "@/i18n";
import { initAnalytics } from "@/lib/analytics";
import { router } from "@/router";
import { MANAGED_STUDIO } from "@/constant/studio";

if (!MANAGED_STUDIO) initAnalytics();
if (MANAGED_STUDIO) document.body.dataset.c = "lime";

document.body.style.fontFamily = MANAGED_STUDIO ? 'Manrope,"PingFang SC","Microsoft YaHei",system-ui,sans-serif' : '"SF Pro Display","SF Pro Text","PingFang SC","Microsoft YaHei","Helvetica Neue",sans-serif';

createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
        <AppProviders>
            <RouterProvider router={router} />
        </AppProviders>
    </React.StrictMode>,
);
