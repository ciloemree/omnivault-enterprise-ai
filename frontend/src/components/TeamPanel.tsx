import { FormEvent, useEffect, useState } from "react";
import { api } from "../lib/api";
import { exportQueries } from "../lib/exportTable";
import { useOmni } from "../lib/store";
import { t } from "../lib/i18n";

type Person = { id: number; username: string; full_name: string; title?: string; role: string; department: string; permission?: string };
type Dept = { slug: string; name: string; users: number; files: number };

export function TeamPanel() {
  const lang = useOmni((s) => s.lang);
  const currentUser = useOmni((s) => s.user);
  const [people, setPeople] = useState<Person[]>([]);
  const [departments, setDepartments] = useState<Dept[]>([]);
  const [deptName, setDeptName] = useState("");
  const [warning, setWarning] = useState("");
  const [passwords, setPasswords] = useState<Record<number, string>>({});
  const [staffDept, setStaffDept] = useState("all");
  const [account, setAccount] = useState({ username: "", password: "", full_name: "", title: "", department: "hr", permission: "editor" });
  const pushToast = useOmni((s) => s.pushToast);
  const askConfirm = useOmni((s) => s.askConfirm);

  useEffect(() => {
    api.users().then(setPeople).catch(() => setPeople([]));
    api.departments().then(setDepartments).catch(() => setDepartments([]));
  }, []);

  async function addDepartment(event: FormEvent) {
    event.preventDefault();
    if (!deptName.trim()) return;
    const rows = await api.addDepartment(deptName.trim());
    setDepartments(rows);
    useOmni.setState({ rooms: rows });
    setDeptName("");
    pushToast(lang === "tr" ? "Departman eklendi" : "Department added", "success");
  }

  async function exportStaff(kind: "csv" | "xlsx") {
    await exportQueries("sorgu-gecmisi", kind, { department: staffDept === "all" ? "" : staffDept });
    pushToast(lang === "tr" ? "Dışa aktarıldı" : "Export ready", "success");
  }

  async function addUser(event: FormEvent) {
    event.preventDefault();
    await api.createUser({ ...account, role: "member" });
    setAccount({ username: "", password: "", full_name: "", title: "", department: departments[0]?.slug || "hr", permission: "editor" });
    setPeople(await api.users());
    setDepartments(await api.departments());
    pushToast(lang === "tr" ? "Personel eklendi" : "Staff member added", "success");
  }

  return (
    <div className="space-y-6">
      <section className="card p-6">
        <h2 className="text-2xl font-semibold">{t(lang, "deptRooms")}</h2>
        {warning && <p className="mt-2 text-sm text-[#b42318]">{warning}</p>}
        <ul className="mt-4 space-y-2">
          {departments.map((item) => (
            <li key={item.slug} className="dept-row flex items-center justify-between rounded-2xl bg-[#f5f5f7] px-4 py-3 text-sm">
              <span>{item.name} · {item.users} kullanıcı · {item.files} dosya</span>
              <span className="flex gap-1">
                <button type="button" className="btn-secondary px-2" onClick={async () => { const rows = await api.reorderDepartment(item.slug, "up"); setDepartments(rows); useOmni.setState({ rooms: rows }); pushToast(lang === "tr" ? "Departman sırası güncellendi" : "Department order updated", "success"); }}>↑</button>
                <button type="button" className="btn-secondary px-2" onClick={async () => { const rows = await api.reorderDepartment(item.slug, "down"); setDepartments(rows); useOmni.setState({ rooms: rows }); pushToast(lang === "tr" ? "Departman sırası güncellendi" : "Department order updated", "success"); }}>↓</button>
              <button
                type="button"
                className="rounded-full bg-[#ff3b30] px-3 py-1.5 text-xs font-semibold text-white"
                onClick={async () => {
                  if (!(await askConfirm())) return;
                  setWarning("");
                  try {
                    const rows = await api.deleteDepartment(item.slug);
                    setDepartments(rows);
                    useOmni.setState({ rooms: rows });
                    pushToast(lang === "tr" ? "Departman silindi" : "Department deleted", "success");
                  } catch (error) {
                    setWarning(error instanceof Error ? error.message : t(lang, "deptBlocked"));
                    pushToast(error instanceof Error ? error.message : t(lang, "deptBlocked"), "error");
                  }
                }}
              >
                {t(lang, "delete")}
              </button>
              </span>
            </li>
          ))}
        </ul>
        <form className="mt-4 flex gap-2" onSubmit={addDepartment}>
          <input className="field settings-control" value={deptName} onChange={(event) => setDeptName(event.target.value)} placeholder={t(lang, "newDept")} />
          <button type="submit" className="btn-primary shrink-0">{t(lang, "newDept")}</button>
        </form>
      </section>

      <section className="card p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-2xl font-semibold">{t(lang, "staffTitle")}</h2>
          <div className="flex gap-2">
            <button type="button" className="btn-secondary" onClick={() => void exportStaff("xlsx")}>{t(lang, "exportExcel")}</button>
            <button type="button" className="btn-secondary" onClick={() => void exportStaff("csv")}>{t(lang, "exportCsv")}</button>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" className={staffDept === "all" ? "rounded-full bg-[#1d1d1f] px-3 py-1.5 text-[13px] text-white" : "rounded-full bg-[#f5f5f7] px-3 py-1.5 text-[13px]"} onClick={() => setStaffDept("all")}>
            {t(lang, "all")} · {people.length}
          </button>
          {departments.map((item) => (
            <button key={item.slug} type="button" className={staffDept === item.slug ? "rounded-full bg-[#1d1d1f] px-3 py-1.5 text-[13px] text-white" : "rounded-full bg-[#f5f5f7] px-3 py-1.5 text-[13px]"} onClick={() => setStaffDept(item.slug)}>
              {item.name} · {people.filter((person) => person.department === item.slug || (item.slug === "yonetim" && person.role === "admin")).length}
            </button>
          ))}
        </div>
        <div className="staff-scroll mt-4 overflow-x-auto">
          <table className="staff-table w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-[#6e6e73]">
              <tr>
                <th className="py-2">{t(lang, "fullName")}</th>
                <th>{t(lang, "jobTitle")}</th>
                <th>{t(lang, "username")}</th>
                <th>{t(lang, "assignedDept")}</th>
                <th>Yetki</th>
                <th>{t(lang, "newPassword")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {people.filter((person) => staffDept === "all" || person.department === staffDept || (staffDept === "yonetim" && person.role === "admin")).map((person) => {
                const root = person.role === "admin";
                const patch = (fields: Partial<Person>) => setPeople((rows) => rows.map((row) => (row.id === person.id ? { ...row, ...fields } : row)));
                return (
                  <tr key={person.id} className="border-t border-black/5">
                    <td className="py-3">
                      <input className="field settings-control" value={person.full_name} onChange={(event) => patch({ full_name: event.target.value })} />
                    </td>
                    <td>
                      <input className="field settings-control" value={person.title || ""} onChange={(event) => patch({ title: event.target.value })} />
                    </td>
                    <td>
                      <input className="field settings-control" value={person.username} autoComplete="off" onChange={(event) => patch({ username: event.target.value })} />
                    </td>
                    <td>
                      {root ? (
                        <span className="chip">{t(lang, "allDeptsShort")}</span>
                      ) : (
                        <select className="field settings-control" value={person.department} onChange={(event) => patch({ department: event.target.value })}>
                          {departments.map((item) => (
                            <option key={item.slug} value={item.slug}>{item.name}</option>
                          ))}
                        </select>
                      )}
                    </td>
                    <td>
                      {root ? (
                        <span className="chip">{t(lang, "permFull")}</span>
                      ) : (
                        <select className="field settings-control" value={person.permission || "editor"} onChange={(event) => patch({ permission: event.target.value })}>
                          <option value="read_only">Sadece Okuma</option>
                          <option value="editor">Ekleme & Okuma</option>
                          <option value="dept_admin">Tam Yetki</option>
                        </select>
                      )}
                    </td>
                    <td>
                      <input
                        className="field settings-control"
                        type="password"
                        autoComplete="new-password"
                        placeholder={t(lang, "newPassword")}
                        value={passwords[person.id] || ""}
                        onChange={(event) => setPasswords((prev) => ({ ...prev, [person.id]: event.target.value }))}
                      />
                    </td>
                    <td>
                      <div className="flex gap-2">
                        <button
                          type="button"
                          className="btn-primary shrink-0"
                          onClick={async () => {
                            setWarning("");
                            try {
                              const password = passwords[person.id] || undefined;
                              await api.updateUser(person.id, {
                                full_name: person.full_name,
                                title: person.title || "",
                                username: person.username,
                                department: root ? undefined : person.department,
                                permission: root ? undefined : person.permission || "editor",
                                password,
                              });
                              setPasswords((prev) => ({ ...prev, [person.id]: "" }));
                              setPeople(await api.users());
                              setDepartments(await api.departments());
                              pushToast(password ? (lang === "tr" ? "Şifre güncellendi" : "Password updated") : (lang === "tr" ? "Personel güncellendi" : "Staff updated"), "success");
                              if (currentUser?.id === person.id) {
                                const next = await api.me();
                                useOmni.setState({ user: { ...next, token: currentUser.token } });
                              }
                            } catch (error) {
                              setWarning(error instanceof Error ? error.message : t(lang, "loginFailed"));
                              setPeople(await api.users());
                            }
                          }}
                        >
                          {t(lang, "updateRow")}
                        </button>
                        {!root && (
                          <button
                            type="button"
                            className="rounded-full bg-[#ff3b30] px-3 py-1.5 text-xs font-semibold text-white"
                            onClick={async () => {
                              if (!(await askConfirm())) return;
                              await api.deleteUser(person.id);
                              setPeople((rows) => rows.filter((row) => row.id !== person.id));
                              setDepartments(await api.departments());
                              pushToast(lang === "tr" ? "Personel silindi" : "Staff member deleted", "success");
                            }}
                          >
                            {t(lang, "delete")}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <form className="card mt-6 p-5" onSubmit={addUser}>
          <h3 className="text-lg font-semibold">{t(lang, "addStaff")}</h3>
          <div className="staff-form mt-4">
            <label>{t(lang, "fullName")}<input className="field" value={account.full_name} onChange={(event) => setAccount((prev) => ({ ...prev, full_name: event.target.value }))} /></label>
            <label>{t(lang, "jobTitle")}<input className="field" value={account.title} onChange={(event) => setAccount((prev) => ({ ...prev, title: event.target.value }))} /></label>
            <label>{t(lang, "username")}<input className="field" autoComplete="off" value={account.username} onChange={(event) => setAccount((prev) => ({ ...prev, username: event.target.value }))} /></label>
            <label>{t(lang, "password")}<input className="field" type="password" autoComplete="new-password" value={account.password} onChange={(event) => setAccount((prev) => ({ ...prev, password: event.target.value }))} /></label>
            <label>{t(lang, "assignedDept")}
              <select className="field" value={account.department} onChange={(event) => setAccount((prev) => ({ ...prev, department: event.target.value }))}>
                {departments.map((item) => (
                  <option key={item.slug} value={item.slug}>{item.name}</option>
                ))}
              </select>
            </label>
            <label>Yetki Seviyesi
              <select className="field" value={account.permission} onChange={(event) => setAccount((prev) => ({ ...prev, permission: event.target.value }))}>
                <option value="read_only">Sadece Okuma</option>
                <option value="editor">Ekleme & Okuma</option>
                <option value="dept_admin">Tam Yetki</option>
              </select>
            </label>
          </div>
          <button type="submit" className="btn-primary mt-4">{t(lang, "addStaff")}</button>
        </form>
      </section>
    </div>
  );
}
