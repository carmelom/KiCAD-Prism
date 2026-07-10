import { Folder, MoreHorizontal, Pencil, Trash2, Image } from "lucide-react";

import { FolderTreeItem, Project } from "@/types/project";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface FolderActionMenuProps {
  folder: FolderTreeItem;
  onRename: (folder: FolderTreeItem) => void;
  onDelete: (folder: FolderTreeItem) => void;
  canManage: boolean;
}

interface ProjectActionMenuProps {
  project: Project;
  projectName: string;
  onMove: (project: Project) => void;
  onDelete: (project: Project) => void;
  onRegenerateThumbnail?: (project: Project) => void;
  canManage: boolean;
}

export function FolderActionMenu({ folder, onRename, onDelete, canManage }: FolderActionMenuProps) {
  if (!canManage) {
    return null;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-muted-foreground"
          onClick={(event) => event.stopPropagation()}
          onPointerDown={(event) => event.stopPropagation()}
          aria-label={`Open actions for folder ${folder.name}`}
        >
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44" onClick={(event) => event.stopPropagation()}>
        <DropdownMenuLabel>Folder</DropdownMenuLabel>
        <DropdownMenuItem
          onClick={(event) => {
            event.stopPropagation();
            onRename(folder);
          }}
        >
          <Pencil className="h-4 w-4" />
          Rename
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          variant="destructive"
          onClick={(event) => {
            event.stopPropagation();
            onDelete(folder);
          }}
        >
          <Trash2 className="h-4 w-4" />
          Delete Folder
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function ProjectActionMenu({ project, projectName, onMove, onDelete, onRegenerateThumbnail, canManage }: ProjectActionMenuProps) {
  if (!canManage) {
    return null;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 bg-background/80 backdrop-blur-sm"
          onClick={(event) => event.stopPropagation()}
          onPointerDown={(event) => event.stopPropagation()}
          aria-label={`Open actions for project ${projectName}`}
        >
          <MoreHorizontal className="h-3.5 w-3.5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52" onClick={(event) => event.stopPropagation()}>
        <DropdownMenuLabel>Project</DropdownMenuLabel>
        <DropdownMenuItem
          onClick={(event) => {
            event.stopPropagation();
            onMove(project);
          }}
        >
          <Folder className="h-4 w-4" />
          Move to Folder
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={(event) => {
            event.stopPropagation();
            onRegenerateThumbnail?.(project);
          }}
        >
          <Image className="h-4 w-4" />
          Regenerate Thumbnail
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          variant="destructive"
          onClick={(event) => {
            event.stopPropagation();
            onDelete(project);
          }}
        >
          <Trash2 className="h-4 w-4" />
          Delete Project
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
