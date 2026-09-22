/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kei Nakayama (kexi)
 */
using System;
using System.Collections.Generic;
using StampFly.Input;
using StampFly.Sim;
using StampFly.World;
using UnityEngine;
using UnityEngine.UIElements;

namespace StampFly.Ui
{
    /// <summary>
    /// Touch-friendly furniture placement using the shared world editing service.
    /// 共通の空間編集処理を使う、タッチ操作対応の家具配置画面。
    /// @design docs/plans/unity-simulator.md §4 [OK]
    /// </summary>
    [RequireComponent(typeof(UIDocument))]
    public sealed class FurnitureEditor : MonoBehaviour
    {
        public const string SaveKey = "stampfly.furniture.world.v1";
        private const float MoveStep = 0.2f;
        private const float TurnStep = 45;
        private WorldLoader loader;
        private WorldEditSession session;
        private SimLoop loop;
        private SimHud hud;
        private FollowCamera follow;
        private Camera flightCamera;
        private Camera editCamera;
        private Camera backgroundCamera;
        private SimControls controls;
        private ChaseCameraControls cameraControls;
        private VisualElement root;
        private VisualElement viewport;
        private Label status;
        private Button undo;
        private string selected;
        private string adding;
        private bool wasPaused;
        private bool controlsEnabled;
        private bool cameraControlsEnabled;
        private bool flightCameraEnabled;
        private bool isEditing;
        private readonly List<Renderer> hiddenCeilings = new List<Renderer>();
        private LineRenderer selection;
        private Material selectionMaterial;

        public bool IsEditing => isEditing;
        public string SelectedId => selected;

        /// <summary>Connect dependencies after bootstrap creates the document. / 起動処理が作った文書へ依存先を接続する。</summary>
        public void Initialize(WorldLoader world, SimLoop simulation, SimHud flightHud, FollowCamera cameraFollow)
        {
            loader = world;
            loop = simulation;
            hud = flightHud;
            follow = cameraFollow;
            flightCamera = follow.GetComponent<Camera>();
            controls = loop.GetComponent<SimControls>();
            cameraControls = follow.GetComponent<ChaseCameraControls>();
            session = new WorldEditSession(loader);
            session.Changed += RefreshSelection;
            hud.FurnitureRequested = Open;
            BuildInterface();
            var cameraObject = new GameObject("FurnitureCamera");
            cameraObject.transform.SetParent(transform, false);
            editCamera = cameraObject.AddComponent<Camera>();
            editCamera.enabled = false;
            editCamera.orthographic = true;
            editCamera.clearFlags = CameraClearFlags.SolidColor;
            editCamera.backgroundColor = new Color(0.055f, 0.07f, 0.09f);
            var backgroundObject = new GameObject("FurnitureBackground");
            backgroundObject.transform.SetParent(transform, false);
            backgroundCamera = backgroundObject.AddComponent<Camera>();
            backgroundCamera.cullingMask = 0;
            backgroundCamera.depth = editCamera.depth - 1;
            backgroundCamera.clearFlags = CameraClearFlags.SolidColor;
            backgroundCamera.backgroundColor = editCamera.backgroundColor;
            backgroundCamera.enabled = false;
        }

        /// <summary>Use one viewport for pointer events; buttons never forward into it. / 床の操作領域を分け、ボタン操作が床へ届かないようにする。</summary>
        private void BuildInterface()
        {
            root = GetComponent<UIDocument>().rootVisualElement;
            root.style.position = Position.Absolute;
            root.style.left = root.style.top = root.style.right = root.style.bottom = 0;
            root.style.display = DisplayStyle.None;
            var choices = new ScrollView(ScrollViewMode.Horizontal);
            choices.verticalScrollerVisibility = ScrollerVisibility.Hidden;
            choices.style.flexShrink = 0;
            choices.style.height = 82;
            choices.style.flexGrow = 0;
            bool hasBrowserLinks = Application.platform == RuntimePlatform.WebGLPlayer;
            choices.style.marginRight = hasBrowserLinks ? 180 : 0;
            choices.contentContainer.style.flexDirection = FlexDirection.Row;
            choices.Add(Action("Select", () => Choose(null)));
            foreach (string type in FurnitureCatalog.Types)
            {
                string kind = type;
                choices.Add(Action(kind, () => Choose(kind)));
            }
            root.Add(choices);
            status = new Label("Choose furniture, then tap the floor. Done restarts at the spawn.");
            status.style.color = Color.white;
            status.style.whiteSpace = WhiteSpace.Normal;
            status.style.marginLeft = status.style.marginRight = 10;
            status.style.fontSize = 13;
            root.Add(status);
            viewport = new VisualElement { name = "furniture-viewport" };
            viewport.style.flexGrow = 1;
            viewport.style.minHeight = 50;
            viewport.RegisterCallback<PointerDownEvent>(PlaceOrSelect);
            root.Add(viewport);
            BuildActions();
        }

        /// <summary>Keep every operation reachable without dragging a tiny handle. / 小さい操作点をドラッグせず全操作へ届くようにする。</summary>
        private void BuildActions()
        {
            var movement = Row();
            movement.Add(Action("Left", () => Move(Vector3.left * MoveStep)));
            movement.Add(Action("Up", () => Move(Vector3.up * MoveStep)));
            movement.Add(Action("Down", () => Move(Vector3.down * MoveStep)));
            movement.Add(Action("Right", () => Move(Vector3.right * MoveStep)));
            movement.Add(Action("Turn 45°", Rotate));
            movement.Add(Action("Delete", Remove));
            undo = Action("Undo", () => Report(session.Undo(), "Undone."));
            movement.Add(undo);
            root.Add(movement);
            var storage = Row();
            storage.Add(Action("Save here", Save));
            storage.Add(Action("Load saved", Load));
            storage.Add(Action("Done & restart", Close));
            root.Add(storage);
            var note = new Label("Saved in this browser only · one layout · move 0.2 m");
            note.style.fontSize = 11;
            note.style.color = Color.gray;
            note.style.marginLeft = 10;
            root.Add(note);
        }

        /// <summary>Wrap action rows on narrow screens. / 狭い画面では操作ボタンを折り返す。</summary>
        private static VisualElement Row()
        {
            var row = new VisualElement();
            row.style.flexDirection = FlexDirection.Row;
            row.style.flexWrap = Wrap.Wrap;
            row.style.justifyContent = Justify.Center;
            row.style.flexShrink = 0;
            return row;
        }

        /// <summary>Reuse the established touch target size. / 既存のタッチ操作領域の大きさを使う。</summary>
        private static Button Action(string text, System.Action callback)
        {
            return TouchFlightControls.ActionButton(text, callback);
        }

        /// <summary>Pause and isolate piloting while furniture is edited. / 家具の編集中は停止して操縦を切り離す。</summary>
        public void Open()
        {
            bool unavailable = isEditing || !loader.HasWorld;
            if (unavailable) return;
            wasPaused = loop.Clock.IsPaused;
            controlsEnabled = controls != null && controls.enabled;
            cameraControlsEnabled = cameraControls != null && cameraControls.enabled;
            flightCameraEnabled = flightCamera.enabled;
            loop.Clock.Pause();
            loader.ResetDynamicBodies();
            (loop.RcSource as KeyboardRc)?.Reset();
            bool hasControls = controls != null;
            if (hasControls) controls.enabled = false;
            bool hasCameraControls = cameraControls != null;
            if (hasCameraControls) cameraControls.enabled = false;
            flightCamera.enabled = false;
            hud.SetEditing(true);
            root.style.display = DisplayStyle.Flex;
            editCamera.enabled = true;
            backgroundCamera.enabled = true;
            isEditing = true;
            Choose(null);
            RefreshSelection();
        }

        /// <summary>Restart at the clear spawn instead of resuming inside newly placed furniture. / 新しい家具内で再開しないよう出発点から再起動する。</summary>
        public void Close()
        {
            bool alreadyClosed = !isEditing;
            if (alreadyClosed) return;
            RestoreView();
            loop.SetSpawn(loader.SpawnPosition, loader.SpawnRotation);
            loop.PowerOn();
            if (wasPaused) loop.Clock.Pause();
            else loop.Clock.Resume();
        }

        /// <summary>Restore precisely the input and camera enable states captured on entry. / 入室時の入力とカメラの有効状態を復元する。</summary>
        private void RestoreView()
        {
            isEditing = false;
            root.style.display = DisplayStyle.None;
            editCamera.enabled = false;
            backgroundCamera.enabled = false;
            flightCamera.enabled = flightCameraEnabled;
            bool hasControls = controls != null;
            if (hasControls) controls.enabled = controlsEnabled;
            bool hasCameraControls = cameraControls != null;
            if (hasCameraControls) cameraControls.enabled = cameraControlsEnabled;
            hud.SetEditing(false);
            if (wasPaused) loop.Clock.Pause();
            else loop.Clock.Resume();
            ClearSelectionOutline();
            foreach (Renderer renderer in hiddenCeilings)
            {
                bool survives = renderer != null;
                if (survives) renderer.enabled = true;
            }
            hiddenCeilings.Clear();
        }

        /// <summary>Fit the entire room inside the remaining touch viewport after a resize. / リサイズ後も操作領域へ部屋全体を収める。</summary>
        private void LateUpdate()
        {
            bool inactive = !isEditing || !loader.HasWorld;
            if (inactive) return;
            loop.Clock.Pause();
            Rect area = viewport.worldBound;
            Rect panel = root.worldBound;
            bool invalidArea = area.width <= 0 || area.height <= 0 || panel.width <= 0 || panel.height <= 0;
            if (invalidArea) return;
            editCamera.rect = new Rect(area.x / panel.width, 1 - area.yMax / panel.height,
                                       area.width / panel.width, area.height / panel.height);
            float[] size = loader.Current.room.size;
            float aspect = area.width / area.height;
            editCamera.orthographicSize = Mathf.Max(size[1], size[0] / aspect) * 0.55f;
            editCamera.transform.SetPositionAndRotation(new Vector3(0, size[2] + 5, 0), Quaternion.Euler(90, 0, 0));
            editCamera.farClipPlane = size[2] + 10;
        }

        /// <summary>The ceiling remains physical; only its drawing is hidden in the plan view. / 天井の衝突形状は維持し、平面図での描画だけを隠す。</summary>
        private void HideCeiling()
        {
            hiddenCeilings.RemoveAll(renderer => renderer == null);
            foreach (ObstacleInfo surface in loader.Surfaces())
            {
                bool isCeiling = surface.ObstacleId == "ceiling";
                if (!isCeiling) continue;
                Renderer renderer = surface.GetComponent<Renderer>();
                bool isVisible = renderer != null && renderer.enabled;
                if (!isVisible) continue;
                renderer.enabled = false;
                hiddenCeilings.Add(renderer);
            }
        }

        /// <summary>A selected catalog item places once, then returns to selection. / 家具を一度配置したら選択操作へ戻す。</summary>
        private void Choose(string type)
        {
            adding = type;
            status.text = type == null
                ? "Tap furniture to select. Done restarts at the spawn."
                : $"Place {type}: tap the floor. Done restarts at the spawn.";
        }

        /// <summary>Convert panel pointer coordinates through the dedicated camera viewport. / 文書上のポインタ座標を専用カメラの表示領域へ変換する。</summary>
        private void PlaceOrSelect(PointerDownEvent evt)
        {
            bool invalid = !isEditing || evt.button != 0;
            if (invalid) return;
            evt.StopPropagation();
            Rect area = viewport.worldBound;
            Vector2 point = evt.position;
            Ray ray = editCamera.ViewportPointToRay(new Vector3(
                (point.x - area.x) / area.width, 1 - (point.y - area.y) / area.height, 0));
            bool placing = !string.IsNullOrEmpty(adding);
            if (placing)
            {
                var floor = new Plane(Vector3.up, Vector3.zero);
                bool hitsFloor = floor.Raycast(ray, out float distance);
                if (!hitsFloor) return;
                Vector3 hit = ray.GetPoint(distance);
                bool added = session.TryAdd(adding, new Vector3(hit.x, hit.z, 0), out string id);
                if (added) { selected = id; adding = null; }
                Report(added, $"Selected {id}. Use move / turn / delete buttons.");
                return;
            }
            SelectAt(ray);
        }

        /// <summary>Ignore the room ceiling and select the closest obstacle surface. / 天井を選択対象から外し、最も近い家具面を選ぶ。</summary>
        private void SelectAt(Ray ray)
        {
            RaycastHit[] hits = Physics.RaycastAll(ray);
            Array.Sort(hits, (left, right) => left.distance.CompareTo(right.distance));
            selected = null;
            foreach (RaycastHit hit in hits)
            {
                var info = hit.collider.GetComponent<ObstacleInfo>();
                bool isObstacle = info != null && WorldObstacleTypes.IsKnown(info.ObstacleType) &&
                                  loader.Find(info.ObstacleId) != null;
                if (!isObstacle) continue;
                selected = info.ObstacleId;
                break;
            }
            RefreshSelection();
            status.text = selected == null ? "Tap furniture to select, or choose furniture above." : $"Selected {selected}. Use move / turn / delete buttons.";
        }

        /// <summary>Use the serialized ENU pose, not a renderer's centre. / 描画物の中心ではなく保存対象のENU位置を使う。</summary>
        private WorldObstacle SelectedObstacle()
        {
            foreach (WorldObstacle obstacle in loader.Current.obstacles)
            {
                bool matches = obstacle.id == selected;
                if (matches) return obstacle;
            }
            return null;
        }

        /// <summary>Move on the floor plane in fixed increments. / 床面上を一定間隔で移動する。</summary>
        private void Move(Vector3 delta)
        {
            WorldObstacle obstacle = SelectedObstacle();
            bool missing = obstacle == null;
            if (missing) { status.text = "Select furniture first."; return; }
            var position = new Vector3(obstacle.position[0], obstacle.position[1], obstacle.position[2]);
            Report(session.TryMove(selected, position + delta), $"Moved {selected}.");
        }

        /// <summary>Rotate around the vertical ENU axis. / ENUの鉛直軸を中心に回転する。</summary>
        private void Rotate() => Report(session.TryRotate(selected, -TurnStep), $"Rotated {selected}.");

        /// <summary>Remove the selected object through the undoable service. / 元に戻せる共通処理で選択物を削除する。</summary>
        private void Remove() => Report(session.TryRemove(selected), "Removed furniture.");

        /// <summary>Report rejected edits without silently changing selection. / 断られた編集を黙らず表示する。</summary>
        private void Report(bool success, string message)
        {
            status.text = success ? message : session.LastError;
            RefreshSelection();
        }

        /// <summary>Store a single explicit local save slot. / 明示的なローカル保存枠を一つ持つ。</summary>
        public void Save()
        {
            try
            {
                PlayerPrefs.SetString(SaveKey, session.ExportJson());
                PlayerPrefs.Save();
                status.text = "Saved in this browser. Saving again replaces this layout.";
            }
            catch (Exception exception) { status.text = "Could not save: " + exception.Message; }
        }

        /// <summary>Import through the same validation as any other world edit. / 他の編集と同じ検証経路で読み戻す。</summary>
        public void Load()
        {
            bool exists = PlayerPrefs.HasKey(SaveKey);
            if (!exists) { status.text = "No layout saved in this browser yet."; return; }
            Report(session.TryImport(PlayerPrefs.GetString(SaveKey)), "Loaded the layout saved in this browser.");
        }

        /// <summary>Rebuild the outline because edits may replace the world's objects. / 編集で物体が置換される場合があるため選択枠を作り直す。</summary>
        private void RefreshSelection()
        {
            if (isEditing) HideCeiling();
            bool ready = undo != null;
            if (ready) undo.SetEnabled(session.CanUndo);
            ClearSelectionOutline();
            bool noSelection = !isEditing || string.IsNullOrEmpty(selected);
            if (noSelection) return;
            GameObject target = loader.Find(selected);
            bool removed = target == null;
            if (removed) { selected = null; return; }
            Collider[] colliders = target.GetComponentsInChildren<Collider>();
            bool empty = colliders.Length == 0;
            if (empty) return;
            Bounds bounds = colliders[0].bounds;
            foreach (Collider collider in colliders) bounds.Encapsulate(collider.bounds);
            DrawSelection(bounds, target.GetComponentInChildren<Renderer>().sharedMaterial);
        }

        /// <summary>Draw a collider-free rectangle above the selected furniture. / 選択した家具の上に衝突形状のない枠を描く。</summary>
        private void DrawSelection(Bounds bounds, Material source)
        {
            var outline = new GameObject("FurnitureSelection");
            outline.transform.SetParent(transform, false);
            selection = outline.AddComponent<LineRenderer>();
            selectionMaterial = new Material(source);
            selectionMaterial.color = new Color(0.1f, 0.95f, 1);
            selection.sharedMaterial = selectionMaterial;
            selection.startColor = selection.endColor = new Color(0.1f, 0.95f, 1);
            selection.startWidth = selection.endWidth = 0.035f;
            selection.loop = true;
            selection.positionCount = 4;
            float y = bounds.max.y + 0.04f;
            selection.SetPositions(new[] { new Vector3(bounds.min.x, y, bounds.min.z),
                new Vector3(bounds.max.x, y, bounds.min.z), new Vector3(bounds.max.x, y, bounds.max.z),
                new Vector3(bounds.min.x, y, bounds.max.z) });
        }

        /// <summary>Release native drawing resources on every selection change. / 選択が変わるたび描画資源を解放する。</summary>
        private void ClearSelectionOutline()
        {
            bool hasOutline = selection != null;
            if (hasOutline) { selection.gameObject.SetActive(false); Release(selection.gameObject); }
            bool hasMaterial = selectionMaterial != null;
            if (hasMaterial) Release(selectionMaterial);
            selection = null;
            selectionMaterial = null;
        }

        /// <summary>Allow cleanup in EditMode tests as well as a running player. / EditMode試験と実行中の両方で後始末できるようにする。</summary>
        private static void Release(UnityEngine.Object target)
        {
            bool isRunning = Application.isPlaying;
            if (isRunning) Destroy(target);
            else DestroyImmediate(target);
        }

        /// <summary>Disabling the tool must not leave the pilot locked out. / 配置画面を無効化しても操縦不能を残さない。</summary>
        private void OnDisable()
        {
            bool canRestore = isEditing && loop != null && hud != null && flightCamera != null;
            if (canRestore) RestoreView();
        }

        /// <summary>Unsubscribe and restore state when the scene is removed. / 場面を破棄するとき購読と状態を後始末する。</summary>
        private void OnDestroy()
        {
            bool open = isEditing && loop != null && hud != null && flightCamera != null;
            if (open) RestoreView();
            bool hasSession = session != null;
            if (hasSession) { session.Changed -= RefreshSelection; session.Dispose(); }
            bool hasHud = hud != null;
            if (hasHud) hud.FurnitureRequested = null;
            ClearSelectionOutline();
        }
    }
}
