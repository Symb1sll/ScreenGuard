"""二分类图像识别训练脚本。

目录格式：
  dataset/
    train/normal/*.jpg
    train/violation/*.jpg
    val/normal/*.jpg
    val/violation/*.jpg

默认使用 MobileNetV3-Small，适合边缘 CPU / 小型 GPU；也可替换为 EfficientNet-B0。
"""
from pathlib import Path
import argparse
import torch
from torch import nn
from torch.utils.data import DataLoader
from torchvision import datasets, transforms, models


def build_model(num_classes=2):
    model = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.IMAGENET1K_V1)
    in_features = model.classifier[-1].in_features
    model.classifier[-1] = nn.Linear(in_features, num_classes)
    return model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--epochs', type=int, default=5)
    ap.add_argument('--batch-size', type=int, default=32)
    ap.add_argument('--lr', type=float, default=3e-4)
    ap.add_argument('--out', default=str(Path(__file__).resolve().parents[4] / 'artifacts/training/mobilenetv3_screen_binary.pt'))
    args = ap.parse_args()

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    tf_train = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.RandomHorizontalFlip(),
        transforms.ColorJitter(0.2, 0.2, 0.2, 0.05),
        transforms.ToTensor(),
        transforms.Normalize([0.485,0.456,0.406],[0.229,0.224,0.225]),
    ])
    tf_val = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize([0.485,0.456,0.406],[0.229,0.224,0.225]),
    ])
    train_ds = datasets.ImageFolder(str(Path(args.data) / 'train'), tf_train)
    val_ds = datasets.ImageFolder(str(Path(args.data) / 'val'), tf_val)
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=4)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, num_workers=4)

    model = build_model().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    loss_fn = nn.CrossEntropyLoss()
    best_acc = 0.0
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    for epoch in range(args.epochs):
        model.train()
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            loss = loss_fn(model(x), y)
            loss.backward()
            opt.step()
        model.eval()
        correct = total = 0
        with torch.no_grad():
            for x, y in val_loader:
                x, y = x.to(device), y.to(device)
                pred = model(x).argmax(1)
                correct += int((pred == y).sum())
                total += int(y.numel())
        acc = correct / max(total, 1)
        print({'epoch': epoch + 1, 'val_acc': acc})
        if acc > best_acc:
            best_acc = acc
            torch.save({'model': model.state_dict(), 'classes': train_ds.classes, 'val_acc': best_acc}, args.out)
    print({'best_val_acc': best_acc, 'saved': args.out})

if __name__ == '__main__':
    main()
