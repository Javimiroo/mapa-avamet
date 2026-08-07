// ============================================================================
// WORKER PER AL MAPA CV (mapa-avamet) — mateix codi que el de Catalunya.
// Desplegament (5 min, al panell de Cloudflare):
//   1. Workers & Pages -> Create -> Worker -> nom: "actualitza-avamet"
//      (la URL ha de quedar https://actualitza-avamet.javiermiroo.workers.dev,
//       que és la que ja porta privat.html)
//   2. Enganxa TOT este fitxer i Deploy.
//   3. Settings -> Variables and secrets:
//        APP_PASS      (Secret) -> la mateixa MAPA_PASS del mapa
//        GITHUB_TOKEN  (Secret) -> PAT fine-grained amb accés al repo
//                                  Javimiroo/mapa-avamet (Actions: Read+Write).
//                                  Pots editar el PAT existent i afegir-li el
//                                  repo nou, o crear-ne un altre.
//        REPO          (Text)   -> Javimiroo/mapa-avamet
//        WORKFLOW      (Text)   -> avamet.yml
//        MIN_MINUTES   (Text)   -> 30
//   4. Settings -> Bindings -> KV namespace -> binding "FOCS" -> crea un KV
//      nou (p. ex. "focs-avamet"). NO reutilitzes el KV de Catalunya o els
//      incendis es barrejarien.
// NOTA: la previsió WindNinja per zona (WORKFLOW_ZONA) encara NO està portada
// al repo de la CV; els botons de previsió donaran error fins a la fase 3.
// ============================================================================

// Cloudflare Worker del mapa privat. Fa DUES coses:
//
//  1) ACTUALITZAR DADES: rep la contrasenya de l'equip, comprova el límit de ritme
//     i dispara la GitHub Action (workflow_dispatch). El token de GitHub queda ACÍ,
//     mai al navegador.
//
//  2) INCENDIS COMPARTITS: guarda els 20 últims incendis (nom, posició de la flama i
//     perímetre) en un KV, perquè els veja TOT l'equip. No toca el repo ni gasta
//     execucions d'Actions.
//
// Variables (Settings -> Variables and secrets):
//   APP_PASS      (Secret)  -> contrasenya que sabrà l'equip
//   GITHUB_TOKEN  (Secret)  -> token fine-grained amb "Actions: Read and write"
//   REPO          (Text)    -> "Javimiroo/mapa-aemet"
//   WORKFLOW      (Text)    -> "privat.yml"   (opcional)
//   MIN_MINUTES   (Text)    -> minuts mínims entre actualitzacions (p. ex. 30)
// Bindings (Settings -> Bindings -> KV namespace):
//   FOCS          -> el KV on es desen els incendis

const MAX_FOCS = 20;

export default {
  async fetch(request, env) {
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };
    const json = (obj, status) =>
      new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json", ...cors } });

    if (request.method === "OPTIONS") return new Response(null, { headers: cors });
    if (request.method !== "POST") return json({ error: "method" }, 405);

    let body = {};
    try { body = await request.json(); } catch (e) {}
    if (!body.pass || body.pass !== env.APP_PASS) return json({ error: "password" }, 403);

    const repo = env.REPO;
    const wf = env.WORKFLOW || "privat.yml";
    const wfZona = env.WORKFLOW_ZONA || "windninja_zona.yml";
    const minMin = parseInt(env.MIN_MINUTES || "60", 10);
    const gh = {
      "Authorization": "Bearer " + env.GITHUB_TOKEN,
      "Accept": "application/vnd.github+json",
      "User-Agent": "graf-actualitzar",
      "X-GitHub-Api-Version": "2022-11-28",
    };

    // ---------------- 3) CAMP DE VENT FI D'UNA ZONA (WindNinja) ----------------
    const accio = body.accio || "";

    // Estat de l'última execució del càlcul de zona (per a saber quan ha acabat
    // sense dependre de la cache del fitxer públic, que va molt endarrerida).
    if (accio === "zona_estat") {
      try {
        const rr = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/${wfZona}/runs?per_page=1`, { headers: gh });
        if (!rr.ok) return json({ ok: true, estat: "?" }, 200);
        const dd = await rr.json();
        const r0 = (dd.workflow_runs || [])[0];
        if (!r0) return json({ ok: true, estat: "cap" }, 200);
        return json({ ok: true, estat: r0.status, resultat: r0.conclusion,
                      creat: r0.created_at, url: r0.html_url }, 200);
      } catch (e) { return json({ ok: true, estat: "?" }, 200); }
    }

    // Llig el fitxer del camp de zona DIRECTAMENT de git (API de continguts), sense
    // passar pel raw.githubusercontent (que va molt endarrerit de memòria cau). Així
    // just després de publicar-se ja el podem desar a l'incendi.
    if (accio === "zona_fitxer") {
      try {
        const rr = await fetch(
          `https://api.github.com/repos/${repo}/contents/vent_zona.enc?ref=zona&t=${Date.now()}`,
          { headers: gh });
        if (!rr.ok) return json({ ok: false, error: "nofile", status: rr.status }, 200);
        const dd = await rr.json();
        let txt = "";
        try { txt = atob(String(dd.content || "").replace(/\s+/g, "")); }
        catch (e) { return json({ ok: false, error: "decode" }, 200); }
        return json({ ok: true, enc: txt, sha: dd.sha }, 200);
      } catch (e) { return json({ ok: false, error: "api" }, 200); }
    }

    if (accio === "zona") {
      const bbox = String(body.bbox || "");
      if (!/^-?\d+(\.\d+)?(,-?\d+(\.\d+)?){3}$/.test(bbox)) return json({ error: "bbox" }, 400);
      const nom = String(body.nom || "").slice(0, 60);
      try {   // si ja n'hi ha un en marxa, no en llancem un altre
        const rr = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/${wfZona}/runs?per_page=3`, { headers: gh });
        if (rr.ok) {
          const dd = await rr.json();
          if ((dd.workflow_runs || []).some(r => r.status !== "completed")) return json({ error: "running" }, 429);
        }
      } catch (e) { /* si falla la comprovació, ho intentem igual */ }
      // mode PREVISIÓ (opcional): estacions virtuals del meteoròleg
      const inputs = { bbox: bbox, nom: nom, mesh: "100" };
      const punts = typeof body.punts === "string" ? body.punts : (body.punts ? JSON.stringify(body.punts) : "");
      if (punts) {
        if (punts.length > 8000) return json({ error: "punts_grans" }, 400);
        try { const arr = JSON.parse(punts); if (!Array.isArray(arr) || !arr.length) return json({ error: "punts" }, 400); }
        catch (e) { return json({ error: "punts_json" }, 400); }
        inputs.punts = punts;
        inputs.hora = String(body.hora || "").slice(0, 32);
        inputs.diurn = body.diurn ? "true" : "false";
      }
      const dz = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/${wfZona}/dispatches`,
        { method: "POST", headers: gh, body: JSON.stringify({ ref: "main", inputs: inputs }) });
      if (dz.status === 204) return json({ ok: true, bbox: bbox }, 200);
      let tz = ""; try { tz = await dz.text(); } catch (e) {}
      return json({ error: "dispatch", status: dz.status, detail: tz.slice(0, 200) }, 502);
    }

    // ---------------- 2) INCENDIS COMPARTITS (KV) ----------------
    if (accio) {
      if (!env.FOCS) return json({ error: "kv", detall: "Falta el binding KV 'FOCS'" }, 500);
      const KEY = "focs";
      const llegir = async () => {
        const t = await env.FOCS.get(KEY);
        try { return t ? JSON.parse(t) : []; } catch (e) { return []; }
      };

      const desar = async (a) => { await env.FOCS.put(KEY, JSON.stringify(a)); };
      // submostreja un perímetre dens a MAX punts SENSE tallar-lo (manté la forma tancada).
      // Abans es feia slice(0,2000): en un foc gran (>2000 vèrtexs) es quedava mig anell
      // i es tancava amb una diagonal recta. Ara agafem 1 de cada N repartits.
      const decimaPunts = (arr, max) => {
        if (!Array.isArray(arr)) return [];
        if (arr.length <= max) return arr;
        const out = [], step = arr.length / max;
        for (let i = 0; i < max; i++) out.push(arr[Math.floor(i * step)]);
        return out;
      };
      // migra el format antic (perim únic) al nou (perims amb historial)
      const migra = (f) => {
        if (!f) return f;
        if (!Array.isArray(f.perims)) {
          f.perims = (Array.isArray(f.perim) && f.perim.length >= 3)
            ? [{ ts: f.ts || Date.now(), pts: f.perim }] : [];
        }
        if (!Array.isArray(f.runs)) f.runs = [];
        return f;
      };

      if (accio === "llista") return json({ ok: true, focs: (await llegir()).map(migra) }, 200);

      if (accio === "guarda") {
        const f = body.foc || {};
        const perims = Array.isArray(f.perims) ? f.perims : [];
        if (!f.flama && perims.length === 0) return json({ error: "buit" }, 400);
        let a = (await llegir()).map(migra);
        const id = f.id || (Date.now().toString(36) + Math.random().toString(36).slice(2, 7));
        const previ = a.find(x => x.id === id);
        const foc = {
          id: id,
          nom: (f.nom || "Incendi").toString().slice(0, 80),
          ts: (previ && previ.ts) || f.ts || Date.now(),
          flama: f.flama || null,
          perims: perims.slice(0, 40).map(p => ({ ts: p.ts || Date.now(), pts: decimaPunts(p.pts || [], 3000) })),
          inici: (f.inici != null) ? f.inici : (previ ? previ.inici : null),
          fi: (f.fi !== undefined) ? f.fi : (previ ? previ.fi : null),
          actiu: (f.actiu !== undefined) ? !!f.actiu : (previ ? !!previ.actiu : false),
          runs: previ && Array.isArray(previ.runs) ? previ.runs : [],
        };
        a = a.filter(x => x.id !== foc.id);
        a.unshift(foc);
        a = a.slice(0, MAX_FOCS);
        await desar(a);
        return json({ ok: true, focs: a }, 200);
      }

      // Desa una correguda de WindNinja lligada a un incendi. El payload xifrat va
      // a la seua pròpia clau (zrun:<rid>) per no inflar l'objecte 'focs'.
      if (accio === "run_add") {
        const id = String(body.id || "");
        const enc = body.enc;                        // string JSON xifrat
        const meta = body.meta || {};
        if (!id || !enc) return json({ error: "params" }, 400);
        let a = (await llegir()).map(migra);
        const foc = a.find(x => x.id === id);
        if (!foc) return json({ error: "nofoc" }, 404);
        const rid = Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
        await env.FOCS.put("zrun:" + rid, typeof enc === "string" ? enc : JSON.stringify(enc));
        foc.runs = foc.runs || [];
        foc.runs.unshift({ rid: rid, ts: meta.ts || Date.now(), bbox: meta.bbox || "",
                           obs: meta.obs || null, malla: meta.malla || null,
                           tipus: meta.tipus || null, hora: meta.hora || null,
                           punts: Array.isArray(meta.punts) ? meta.punts.slice(0, 50) : null });
        // límit de corregudes per incendi: esborrem les més antigues
        const MAXR = 40;
        if (foc.runs.length > MAXR) {
          for (const r of foc.runs.slice(MAXR)) { try { await env.FOCS.delete("zrun:" + r.rid); } catch (e) {} }
          foc.runs = foc.runs.slice(0, MAXR);
        }
        await desar(a);
        return json({ ok: true, focs: a, rid: rid }, 200);
      }

      // esborra UNA correguda concreta (p.ex. una previsió) i la seua clau zrun
      if (accio === "run_del") {
        const id = String(body.id || ""), rid = String(body.rid || "");
        if (!id || !rid) return json({ error: "params" }, 400);
        let a = (await llegir()).map(migra);
        const foc = a.find(x => x.id === id);
        if (!foc) return json({ error: "nofoc" }, 404);
        foc.runs = (foc.runs || []).filter(r => r.rid !== rid);
        try { await env.FOCS.delete("zrun:" + rid); } catch (e) {}
        await desar(a);
        return json({ ok: true, focs: a }, 200);
      }

      // marca/desmarca un incendi com a ACTIU (compartit per tot l'equip)
      if (accio === "foc_actiu") {
        const id = String(body.id || "");
        if (!id) return json({ error: "params" }, 400);
        let a = (await llegir()).map(migra);
        const foc = a.find(x => x.id === id);
        if (!foc) return json({ error: "nofoc" }, 404);
        foc.actiu = !!body.actiu;
        await desar(a);
        return json({ ok: true, focs: a }, 200);
      }

      if (accio === "run_get") {
        const rid = String(body.rid || "");
        if (!rid) return json({ error: "params" }, 400);
        const t = await env.FOCS.get("zrun:" + rid);
        if (!t) return json({ error: "notfound" }, 404);
        return json({ ok: true, enc: t }, 200);
      }

      if (accio === "esborra") {
        let a = (await llegir()).map(migra);
        const foc = a.find(x => x.id === body.id);
        if (foc && Array.isArray(foc.runs)) {          // esborrem també les corregudes associades
          for (const r of foc.runs) { try { await env.FOCS.delete("zrun:" + r.rid); } catch (e) {} }
        }
        a = a.filter(x => x.id !== body.id);
        await desar(a);
        return json({ ok: true, focs: a }, 200);
      }
      return json({ error: "accio" }, 400);
    }

    // ---------------- 1) ACTUALITZAR DADES (workflow_dispatch) ----------------
    // El límit es compta des de l'última execució AMB ÈXIT: una execució fallida
    // no ha de bloquejar el reintent.
    try {
      const runsR = await fetch(
        `https://api.github.com/repos/${repo}/actions/workflows/${wf}/runs?per_page=5`,
        { headers: gh });
      if (runsR.ok) {
        const d = await runsR.json();
        const runs = d.workflow_runs || [];
        if (runs.some(r => r.status !== "completed")) return json({ error: "running" }, 429);
        const okRun = runs.find(r => r.conclusion === "success");
        if (okRun) {
          const mins = (Date.now() - Date.parse(okRun.created_at)) / 60000;
          if (mins < minMin) return json({ error: "rate", wait: Math.ceil(minMin - mins) }, 429);
        }
      }
    } catch (e) { /* si falla la comprovació, continuem i intentem disparar */ }

    const disp = await fetch(
      `https://api.github.com/repos/${repo}/actions/workflows/${wf}/dispatches`,
      { method: "POST", headers: gh, body: JSON.stringify({ ref: "main" }) });

    if (disp.status === 204) return json({ ok: true }, 200);
    let txt = ""; try { txt = await disp.text(); } catch (e) {}
    return json({ error: "dispatch", status: disp.status, detail: txt.slice(0, 200) }, 502);
  },
};
