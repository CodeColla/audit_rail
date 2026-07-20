import axios from "axios";

export const api = axios.create({ baseURL: "/api" });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("ar_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401 && !location.pathname.startsWith("/login")) {
      // Clear BOTH — the session identity lives in ar_user, so leaving it behind
      // makes the app think it's still logged in and bounce /login -> / forever.
      localStorage.removeItem("ar_token");
      localStorage.removeItem("ar_user");
      location.href = "/login";
    }
    return Promise.reject(err);
  },
);

// tiny query helper: returns the data of a GET
export const get = <T,>(url: string) => api.get<T>(url).then((r) => r.data);
