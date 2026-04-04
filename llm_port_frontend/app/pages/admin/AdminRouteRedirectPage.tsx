import { useEffect, useState } from "react";
import { Navigate } from "react-router";
import { auth } from "~/api/auth";

export default function AdminRouteRedirectPage() {
  const [target, setTarget] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    auth
      .me()
      .then((user) => {
        if (cancelled) return;
        setTarget(user.is_superuser ? "/admin/dashboard" : "/chat");
      })
      .catch(() => {
        if (cancelled) return;
        setTarget("/login?next=%2Fadmin");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!target) return null;
  return <Navigate to={target} replace />;
}
