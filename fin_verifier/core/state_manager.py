import os
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fin_verifier.core.config import TASKS_DATA_DIR
from fin_verifier.core.models import ResearchTask, TaskStatus, TaskSummaryItem

logger = logging.getLogger("fin_verifier.core.state_manager")

class TaskStateManager:
    """
    任务状态持久化管理器。
    采用单文件原子替换写入 (写入同目录临时文件后使用 os.replace 替换)，
    配合进程内线程锁确保并发安全，服务重启后能够完整反序列化回溯。
    """
    _lock = threading.Lock()

    @classmethod
    def get_task_file(cls, task_id: str) -> Path:
        # 防止路径遍历攻击，仅允许合法的文件名格式
        safe_task_id = "".join(c for c in task_id if c.isalnum() or c in ("-", "_"))
        return TASKS_DATA_DIR / f"{safe_task_id}.json"

    @classmethod
    def save_task(cls, task: ResearchTask) -> None:
        """原子落盘保存任务状态"""
        with cls._lock:
            task.updated_at = datetime.now().isoformat()
            file_path = cls.get_task_file(task.task_id)
            tmp_path = file_path.with_suffix(".tmp")
            
            data_str = task.model_dump_json(indent=2)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(data_str)
            
            # 原子替换
            os.replace(tmp_path, file_path)
            logger.info(f"任务 [{task.task_id}] 状态已原子落盘 (状态: {task.status.value})")

    @classmethod
    def load_task(cls, task_id: str) -> Optional[ResearchTask]:
        """从磁盘加载指定任务"""
        file_path = cls.get_task_file(task_id)
        if not file_path.exists():
            return None
        
        with cls._lock:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                return ResearchTask.model_validate_json(content)
            except Exception as e:
                logger.error(f"加载任务 [{task_id}] 失败: {e}")
                return None

    @classmethod
    def list_tasks(cls, starred_only: bool = False) -> List[TaskSummaryItem]:
        """获取任务摘要列表，支持按收藏状态筛选"""
        summaries: List[TaskSummaryItem] = []
        if not TASKS_DATA_DIR.exists():
            return summaries

        with cls._lock:
            for json_file in sorted(TASKS_DATA_DIR.glob("*.json"), key=os.path.getmtime, reverse=True):
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    if starred_only and not data.get("starred", False):
                        continue

                    report = data.get("report")
                    verdict_status = report.get("verdict_status") if report else None
                    confidence_level = report.get("confidence_level") if report else None

                    summaries.append(TaskSummaryItem(
                        task_id=data.get("task_id", ""),
                        status=data.get("status", TaskStatus.CREATED),
                        starred=data.get("starred", False),
                        tags=data.get("tags", []),
                        ticker=data.get("ticker", ""),
                        ticker_name=data.get("ticker_name", ""),
                        hypothesis=data.get("clarified_hypothesis", data.get("original_hypothesis", "")),
                        verdict_status=verdict_status,
                        confidence_level=confidence_level,
                        created_at=data.get("created_at", ""),
                        updated_at=data.get("updated_at", "")
                    ))
                except Exception as e:
                    logger.warning(f"读取任务摘要文件 {json_file.name} 失败: {e}")
                    continue

        return summaries

    @classmethod
    def set_star_and_tags(
        cls,
        task_id: str,
        starred: Optional[bool] = None,
        tags: Optional[List[str]] = None
    ) -> Optional[ResearchTask]:
        """更新任务的收藏状态与自定义标签（标签自动去重）"""
        task = cls.load_task(task_id)
        if not task:
            return None
        
        if starred is not None:
            task.starred = starred
        if tags is not None:
            # 严格去重且保留顺序
            unique_tags = list(dict.fromkeys(tags))
            task.tags = unique_tags

        cls.save_task(task)
        return task

    @classmethod
    def cleanup_orphan_tasks(cls) -> int:
        """
        服务启动生命周期调用：扫描磁盘上的所有任务文件，
        将停留在非终态（非 COMPLETED / PARTIALLY_COMPLETED / FAILED）的僵尸任务
        统一收敛标记为 FAILED (error_message="服务重启导致未完成任务中断")。
        返回收敛清理的任务数量。
        """
        if not TASKS_DATA_DIR.exists():
            return 0

        terminal_statuses = {
            TaskStatus.COMPLETED,
            TaskStatus.PARTIALLY_COMPLETED,
            TaskStatus.FAILED
        }
        cleaned_count = 0

        with cls._lock:
            for json_file in TASKS_DATA_DIR.glob("*.json"):
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    task = ResearchTask.model_validate_json(content)
                    if task.status not in terminal_statuses:
                        task.status = TaskStatus.FAILED
                        task.error_message = "服务重启导致未完成任务中断"
                        task.updated_at = datetime.now().isoformat()

                        tmp_path = json_file.with_suffix(".tmp")
                        with open(tmp_path, "w", encoding="utf-8") as f:
                            f.write(task.model_dump_json(indent=2))
                        os.replace(tmp_path, json_file)
                        cleaned_count += 1
                        logger.info(f"收敛孤儿中断任务: {task.task_id} -> FAILED")
                except Exception as e:
                    logger.warning(f"扫描任务文件失败 {json_file.name}: {e}")

        return cleaned_count
